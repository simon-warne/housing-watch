#!/usr/bin/env bash
# Last step of a failed watch run: one ntfy alert per failure streak (Simon, 2026-11-28).
# The workflow runs it only on a run's first attempt; here it is skipped when the previous
# completed watch run failed too (that one already alerted). Cancelled and skipped runs (dropped
# from the concurrency group) and test-alert runs (run-name "watch test alert") don't count.
# Only main's runs count (Simon, 2026-11-28): the workflow skips this step on other branches.
# If the run list can't be read, it alerts anyway: better a second alert than none.
# Env: NTFY_TOPIC (secret, never printed: ntfy's reply echoes it and goes to /dev/null),
# GH_TOKEN, REPO, RUN_ID, RUN_URL.
set -uo pipefail
: "${NTFY_TOPIC:?NTFY_TOPIC secret is not set}"

prev=$(gh run list -R "$REPO" --workflow watch.yml --limit 20 \
  --branch main --json databaseId,status,conclusion,displayTitle 2>/dev/null |
  python3 -c '
import json, sys
me = int(sys.argv[1])
runs = [
    r for r in json.load(sys.stdin)
    if r["databaseId"] < me and r["status"] == "completed" and r["displayTitle"] == "watch"
    and r["conclusion"] not in ("cancelled", "skipped")
]
print(max(runs, key=lambda r: r["databaseId"])["conclusion"] if runs else "")
' "$RUN_ID" 2>/dev/null) || prev=""

if [ "$prev" = "failure" ]; then
  echo "previous watch run failed too: already alerted"
  exit 0
fi

curl -sSf -o /dev/null \
  -H "Title: housing-watch: run failed" \
  -H "Priority: high" \
  -H "Tags: warning" \
  -H "Click: $RUN_URL" \
  -d "housing-watch: run failed $RUN_URL" \
  "https://ntfy.sh/$NTFY_TOPIC"
echo "failure alert sent"
