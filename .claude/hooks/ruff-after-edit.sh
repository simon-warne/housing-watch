#!/usr/bin/env bash
# PostToolUse (Edit|Write): format and lint the edited Python file.
f=$(jq -r '.tool_input.file_path // empty')
[[ "$f" == *.py ]] || exit 0
cd "$CLAUDE_PROJECT_DIR" || exit 0
uv run -q ruff format "$f" >/dev/null 2>&1
out=$(uv run -q ruff check --fix "$f" 2>&1) || { echo "$out" >&2; exit 2; }
exit 0
