#!/usr/bin/env bash
# M4 acceptance evidence: scheduled watch runs over the last N days (default 7) and every gap
# over 45 min between run starts. Needs `gh` logged in. Prints no secrets (there are none here).
set -euo pipefail
days=${1:-7}
gh run list --workflow watch.yml --event schedule --limit 1000 \
  --json createdAt,conclusion |
  uv run --no-dev python -c '
import json, sys
from datetime import datetime, timedelta, UTC
days = int(sys.argv[1])
since = datetime.now(UTC) - timedelta(days=days)
runs = sorted(
    (datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00")), r["conclusion"])
    for r in json.load(sys.stdin)
)
runs = [r for r in runs if r[0] >= since]
bad = [c for _, c in runs if c not in ("success", None, "")]
print(f"{len(runs)} scheduled runs since {since:%Y-%m-%d %H:%M} UTC, {len(bad)} not successful")
gaps = [(a, b) for (a, _), (b, _) in zip(runs, runs[1:]) if b - a > timedelta(minutes=45)]
for a, b in gaps:
    print(f"gap {a:%Y-%m-%d %H:%M} -> {b:%H:%M} UTC ({int((b - a).total_seconds() // 60)} min)")
print(f"{len(gaps)} gaps over 45 min")
' "$days"
