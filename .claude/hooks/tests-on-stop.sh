#!/usr/bin/env bash
# Stop: run the fast tests; on failure, block stopping once and show Claude the output.
active=$(jq -r '.stop_hook_active // false')
[ "$active" = "true" ] && exit 0
cd "$CLAUDE_PROJECT_DIR" || exit 0
[ -n "$(find tests -name 'test_*.py' 2>/dev/null)" ] || exit 0
out=$(uv run -q pytest -q -x 2>&1) && exit 0
echo "$out" | tail -30 >&2
exit 2
