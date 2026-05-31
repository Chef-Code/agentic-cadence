# Audit Replay Design

## Objective

Add a read-only `audit-replay` command that verifies the local append-only
Cadence audit log enough to gate future execution work. The command should make
corrupt, unsupported, or malformed audit history visible before Cadence grows
real executor invocation or active-loop controls.

This is the next Phase 1 safety slice after local policy and audit writes. It
does not make Cadence autonomous; it makes the audit trail inspectable.

## Scope

The command reads `<root>/audit/events.jsonl` by default and emits a single
`audit-replay.v1` JSON packet. It must not append audit records, run executors,
mutate Git state, create branches, commit, push, open pull requests, spend
review, merge, or repair files.

Missing or empty audit files are valid for a fresh runtime root. They report
zero records and no blockers, but they are not proof that older audit history
was preserved or clean.

## Packet Contract

Successful command output uses this shape:

```json
{
  "protocol_version": "v1",
  "schema_version": "audit-replay.v1",
  "packet": "audit_replay",
  "audit_path": "<absolute normalized path string>",
  "audit_exists": true,
  "valid": true,
  "lines_seen": 2,
  "records_seen": 2,
  "records_valid": 2,
  "records_invalid": 0,
  "events_by_type": {
    "loop_tick_decision": 1,
    "executor_result_validation": 1
  },
  "blockers": [],
  "recommended_next_action": "use_audit_replay_evidence"
}
```

Invalid replay packets keep the same top-level shape, set `valid: false`, and
exit nonzero. The recommendation is command-local and must not be interpreted
as approval to run an executor, continue an epoch, or bypass operator approval.
Use `recommended_next_action: "upgrade_cadence"` only when every blocker is
`audit_schema_version_unsupported` or `audit_event_unsupported`. Use
`recommended_next_action: "inspect_audit_log"` for corruption, malformed
records, unreadable files, missing or malformed schema fields, or mixed
unsupported and corrupt history.

Each blocker is an object with:

- `code`: stable machine-readable reason.
- `message`: concise human-readable reason.
- `line`: one-based JSONL line number for line-scoped blockers. File-scoped
  blockers omit `line`.

## Validation Rules

The replay validator should parse the file line by line so it can report
line-numbered blockers without loading the whole log into memory. Blank lines
are invalid because the writer emits exactly one compact JSON object per line.

Common record rules:

- record must be a JSON object;
- `schema_version` must be present and a string;
- unsupported schema strings produce `audit_schema_version_unsupported` and
  short-circuit before all `cadence-audit.v1` field validation;
- supported records must have `schema_version: cadence-audit.v1`;
- `recorded_at` must be a non-empty string;
- `event` must be a non-empty string;
- unsupported event strings produce `audit_event_unsupported` after common
  `cadence-audit.v1` field validation, then short-circuit before
  event-specific required-field validation;
- every present key ending in `_checksum`, plus event-required checksum fields,
  must be a string with the `sha256:` prefix and a 64-character lowercase hex
  digest.

Supported event rules:

- `loop_tick_decision` requires non-empty string fields `tick_id` and
  `action`, non-empty binding string fields `reason`, `repo`, `branch`,
  `head`, and `snapshot_id`, boolean field `operator_confirmation_required`,
  and valid checksum field `payload_checksum`.
- `executor_result_validation` requires non-empty string field `action`,
  non-empty string fields `reason`, `task_file`, and `result_file`, boolean
  field `valid`, and valid checksum fields `payload_checksum`,
  `task_packet_checksum`, and `result_evidence_checksum`. When `valid` is
  `true`, it also requires non-empty binding string fields `task_id`, `repo`,
  `branch`, and `head`. When `valid` is `false`, those fields may be omitted
  because malformed task packets can prevent Cadence from extracting trusted
  task or repo anchors.

Fields not listed as common or event-specific requirements are optional.
`executor_task_id` stays optional for `loop_tick_decision`. `task_id`, `repo`,
`branch`, and `head` are optional only for `executor_result_validation` records
where `valid` is `false`, because malformed task packets can prevent Cadence
from extracting trusted task or repo anchors. The first replay packet does not
include per-record summaries.

Counting rules:

- `lines_seen` is the number of physical lines read from `events.jsonl`.
- `records_seen` equals `records_valid + records_invalid`; every non-missing
  line contributes exactly one valid or invalid record outcome, including blank
  lines, invalid JSON, arrays, scalars, unsupported events, and records with
  multiple blockers.
- `records_invalid` increments once per invalid line, not once per blocker.
- `events_by_type` counts only fully valid records by supported event name.
- file-scoped failures that happen before record parsing, such as a non-regular
  audit path, unreadable file, or UTF-8 decode failure, return `lines_seen: 0`,
  `records_seen: 0`, `records_valid: 0`, `records_invalid: 0`, and
  `events_by_type: {}`. Missing audit files are a separate valid zero-record
  case. Decode failures discard partial read progress because replay cannot
  trust a partially decoded audit stream as complete evidence.

The initial replay command validates record shape and checksum syntax. It does
not recompute `payload_checksum`, `task_packet_checksum`, or
`result_evidence_checksum` from original packet bodies because the compact audit
record intentionally does not store those bodies.

## Blocker Codes

Initial blocker codes:

- `audit_path_not_file`
- `audit_file_unreadable`
- `audit_file_decode_failed`
- `audit_line_blank`
- `audit_line_invalid_json`
- `audit_record_not_object`
- `audit_schema_version_missing`
- `audit_schema_version_type_invalid`
- `audit_schema_version_unsupported`
- `audit_event_missing`
- `audit_event_type_invalid`
- `audit_event_unsupported`
- `audit_required_field_missing`
- `audit_field_type_invalid`
- `audit_checksum_invalid`

These codes are enough for tests and future policy gates to distinguish
corruption from unsupported future schema or event records.

## Architecture

Add replay helpers to `codex_cadence/policy_audit.py` near the existing audit
writer functions:

- `AUDIT_REPLAY_SCHEMA_VERSION = "audit-replay.v1"`
- `replay_audit_log(root: Path) -> dict[str, Any]`
- small private helpers for checksum validation, required-field validation, and
  blocker construction.

Wire a new `audit-replay` subcommand in `codex_cadence/cli.py`. It should use
the global `--root` value and print the replay packet as JSON. It needs runtime
root resolution and `runtime_root_location_safety_issue()` protection, but it
must not run `runtime_root_safety_issue()` against `Path.cwd()` because
`audit-replay` has no target repository `cwd`.

The helper should report `audit_path` as
`(root / "audit" / "events.jsonl").expanduser().resolve(strict=False)`, after
the CLI has resolved `root`, so missing audit files still get deterministic
absolute paths without forcing file creation.

Implementation note for the current dispatcher: this needs an explicit
root-only dispatch path, such as a `guards_runtime_root_only` flag, because
`requires_root=False` plus `guards_optional_root=True` does not resolve the
default root when `--root` is omitted. The dispatcher should resolve the
supplied or default root, run `runtime_root_location_safety_issue()`, and skip
`runtime_root_safety_issue()` because there is no target repository `cwd`.

## Tests

Add focused tests in `tests/test_audit_replay.py`, following existing CLI packet
tests while keeping the new command coverage isolated from the already large
Cadence CLI test module.

Required coverage:

- missing audit log returns a valid zero-record packet;
- empty audit log returns a valid zero-record packet;
- missing audit log does not create `audit/` or `events.jsonl`;
- valid `loop_tick_decision` record is counted;
- valid `executor_result_validation` record is counted;
- non-file audit path returns `audit_path_not_file`;
- unreadable audit file returns `audit_file_unreadable`;
- undecodable audit bytes return `audit_file_decode_failed`;
- bad JSON line returns `audit_line_invalid_json` with line number;
- blank line returns `audit_line_blank`;
- JSON array or scalar line returns `audit_record_not_object`;
- missing schema returns `audit_schema_version_missing`;
- non-string schema returns `audit_schema_version_type_invalid`;
- unsupported schema returns `audit_schema_version_unsupported`, skips v1 field
  validation, and recommends `upgrade_cadence` when no corruption blockers are
  present;
- missing event returns `audit_event_missing`;
- unsupported event returns `audit_event_unsupported`;
- wrong required field type returns `audit_field_type_invalid`;
- missing required checksum returns `audit_required_field_missing`;
- malformed checksum returns `audit_checksum_invalid`;
- valid `loop_tick_decision` records require repo, branch, head, snapshot id,
  and reason binding fields;
- valid successful `executor_result_validation` records require task id, repo,
  branch, and head binding fields, while invalid malformed-task records may omit
  those anchors and still replay as valid audit records;
- mixed valid and invalid lines report line, record, and event counts using the
  counting rules above and exit nonzero;
- repo-local unignored runtime root is rejected unless
  `--allow-repo-local-root` is supplied.

Run at minimum:

```text
python -m unittest tests.test_cadence tests.test_audit_replay
python scripts/validate_protocol.py
python -m py_compile codex_cadence/policy_audit.py codex_cadence/cli.py
git diff --check
```

## Documentation

Update `docs/protocol.md` to define `audit-replay.v1` and its read-only
guarantee. Update `docs/implementation-slices.md` so audit replay summary and
corrupted audit handling move from missing evidence to complete for Phase 1.
Evaluate `docs/roadmap.md`, `docs/autonomous-loop-readiness.md`,
`docs/decision-log.md`, `docs/progress-log.md`, `SKILL.md`, and
`scripts/validate_protocol.py` for required living-document and protocol-token
updates. Add a short README example only if the command is useful enough to
expose in the first-run workflow; otherwise keep the README unchanged for this
slice and record why in the progress log.

## Non-Goals

- No hash chaining.
- No audit repair command.
- No remote or tamper-evident backend.
- No executor invocation.
- No command allow/deny policy.
- No branch policy.
- No payload reconstruction from compact checksum records.
