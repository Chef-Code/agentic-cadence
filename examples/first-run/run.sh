#!/usr/bin/env sh
set -eu

# Runtime output: examples/first-run/work
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
WORK_DIR="$SCRIPT_DIR/work"
TARGET_REPO="$WORK_DIR/repo"
RUNTIME_ROOT="$WORK_DIR/runtime"

rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR"
cp -R "$SCRIPT_DIR/repo" "$TARGET_REPO"

git -C "$TARGET_REPO" init -b main >/dev/null
git -C "$TARGET_REPO" config user.email "demo@example.com"
git -C "$TARGET_REPO" config user.name "Cadence Demo"
git -C "$TARGET_REPO" add README.md docs/cadence/business-memory.md
git -C "$TARGET_REPO" commit -m "Initial example repo" >/dev/null

cadence() {
  if [ -n "${CODEX_CADENCE_PYTHON:-}" ]; then
    "$CODEX_CADENCE_PYTHON" -m codex_cadence "$@"
  else
    agentic-cadence "$@"
  fi
}

cadence --root "$RUNTIME_ROOT" status >/dev/null
cadence --root "$RUNTIME_ROOT" create-handoff \
  --id read-the-repo \
  --title "Read the repo" \
  --repo local/example \
  --branch main \
  --task-type discovery \
  --message-file "$SCRIPT_DIR/handoff.md" >/dev/null
cadence --root "$RUNTIME_ROOT" next-handoff >/dev/null
cadence --root "$RUNTIME_ROOT" claim-handoff read-the-repo --claimer demo >/dev/null
cadence --root "$RUNTIME_ROOT" complete-handoff read-the-repo --summary "first run completed" >/dev/null
DISCOVERY_OUTPUT=$(cadence --root "$RUNTIME_ROOT" discover-candidates --cwd "$TARGET_REPO" --intent hybrid --discovery-mode local --elect)
printf '%s' "$DISCOVERY_OUTPUT" | "${CODEX_CADENCE_PYTHON:-python}" -c 'import json, sys; data = json.load(sys.stdin); count = data.get("sources", {}).get("business_memory", 0); assert count > 0, "sources.business_memory must be greater than zero"'

printf '%s\n' "Agentic Cadence first-run example completed."
