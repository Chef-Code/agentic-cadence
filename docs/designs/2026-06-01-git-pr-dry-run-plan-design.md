# Git/PR Dry-Run Planning Design

Date: 2026-06-01
Status: approved direction, pending implementation plan

## Context

Agentic Cadence can validate generic executor task and result evidence, and it can evaluate draft PR bodies and saved PR readiness inputs. It cannot yet turn successful executor evidence into a governed branch, commit, push, or pull request workflow.

The next slice starts the Minimal Git/PR Automation roadmap item with a local dry-run contract only. Cadence should produce a deterministic plan for the Git/PR transition without mutating Git state, calling GitHub, pushing, or creating a pull request.

Although this slice operates on a single executor result, the dry-run Git/PR planning packet is designed as a future coordination artifact for multi-agent workflows. In later phases, builder agents, reviewer agents, QA agents, documentation agents, or human operators may consume this packet to decide whether to create a branch, commit, pull request, request review, update docs, or hand off work to another role.

## Core Invariants

- This slice remains dry-run only.
- Suggested commands are never executed by Cadence.
- The executor that produced result evidence is not the final authority for Git/PR approval.
- The packet must support future role separation between builder, reviewer, QA, release, documentation, and human operator roles.
- Any future live Git or GitHub behavior must be added behind explicit approval and stable packet contracts.

## Approaches Considered

Recommended: add a dry-run planning command that reads an executor task packet and result evidence, inspects local Git state, generates branch/commit/PR body recommendations, and runs PR body preflight. This gives reviewers a stable packet and tests before any live side effects exist.

Alternative: add optional live flags for commit, push, or `gh pr create`. This is too much for the first slice because it introduces credentials, irreversible remote side effects, branch ownership, and approval semantics before the packet contract is proven.

Alternative: extend `validate-executor-result` to emit PR planning fields. This would mix result validation with next-action planning and make it harder to reason about side-effect-free validation versus post-success workflow decisions.

## Command

Add a new CLI command named `git-pr-plan`.

Initial arguments:

- `--cwd`: target repository path, defaulting to the current directory.
- `--task-file`: generic executor task packet JSON.
- `--result-file`: generic executor result evidence JSON.
- `--base-branch`: target PR base branch, default `main`.
- `--branch-prefix`: generated branch prefix, default `cadence`.
- `--pr-template-file`: optional local PR template file.
- `--required-body-section`: repeatable fallback section contract, matching `pr-body-preflight`.

The command does not require a runtime root. It reads local files and local Git refs only.

## Packet

The command returns a packet with `schema_version: "git-pr-plan.v1"`, `dry_run: true`, `operator_confirmation_required: true`, and `side_effects: []`.

When the plan is valid, the packet includes:

- task id, title, and summary copied from the task packet;
- repository path, current branch, current head, base branch, and worktree status;
- proposed branch name;
- proposed commit message;
- proposed PR title;
- generated PR body;
- PR body preflight packet;
- recommended next action: `review_git_pr_plan`;
- explicit shell commands as suggested commands only, not executed commands.

When blocked, the packet includes stable blocker codes and recommends `address_blockers`.

Plan validity is not Git/PR transition approval. In the current executor contract, task packets still forbid commit, push, PR creation, and head-change permissions. The v1 planning packet can turn successful evidence into a reviewable transition plan, but it must not claim that a branchable commit already exists unless a later contract explicitly adds materialized-change or commit evidence.

## Validation

The command must validate the executor task and result with existing contract helpers before planning.

Planning is ready only when:

- task packet validation passes;
- result evidence validation passes;
- result status is `succeeded`;
- result `resulting_head` matches the current local `HEAD`;
- the current repo path matches the task packet repo path;
- the worktree is clean;
- changed files in result evidence are non-empty and were already accepted by the executor contract;
- the base branch name and generated branch name are valid Git ref names;
- PR body preflight is ready when a template or required sections are supplied.

Planning readiness means the packet is safe to review, not safe to execute. The operator or a separate future role still owns the decision to materialize changes, create a branch, commit, push, or open a pull request.

Blocked examples:

- invalid task packet;
- invalid result evidence;
- non-success result status;
- missing or mismatched resulting head;
- dirty worktree;
- wrong repository path;
- invalid branch name;
- missing PR template sections.

## Generated Text

Branch name format:

```text
<branch-prefix>/<task-id-slug>
```

Commit message format:

```text
<task title>
```

PR title format:

```text
<task title>
```

PR body format:

```markdown
## Summary

<executor result summary>

## Task

- Task: `<task id>`
- Source: `<task source>`

## Files Changed

- `<path>`

## Validation

- `<validation name>`: `<status>`

## Safety

- Dry run only.
- No branch, commit, push, or pull request was created by Cadence.
```

If a PR template supplies additional required sections, the first slice does not synthesize arbitrary content for unknown headings. It reports missing sections through preflight so the operator can update the body or the future implementation can add template-aware filling deliberately.

## Non-Goals

- No `git checkout`, `git switch`, `git commit`, `git push`, or `gh pr create`.
- No GitHub API calls.
- No live PR readiness fetching.
- No branch ownership lock.
- No remote audit record.
- No merge, release, or package publication behavior.

## Future Extensions

Later slices may add:

- branch ownership locks;
- issue claiming or assignment;
- reviewer-agent handoff packets;
- QA-agent validation packets;
- documentation-agent update checks;
- draft PR creation after operator approval;
- live GitHub PR creation behind explicit confirmation;
- multi-agent conflict detection when two tasks touch overlapping files;
- merge readiness decisions based on CI, reviews, docs, and policy.

Do not build these extensions in this slice. Design the dry-run packet so it does not block those future moves.

## Tests

Add focused unit and CLI tests:

- ready dry-run packet from a successful executor result;
- CLI does not call `gh` or mutate Git state;
- blocked invalid task packet;
- blocked invalid result evidence;
- blocked non-success result;
- blocked dirty worktree;
- blocked current `HEAD` mismatch;
- blocked missing PR template section;
- branch name sanitization and invalid branch name handling.

Run the relevant focused tests first, then the full local validation set before any PR.
