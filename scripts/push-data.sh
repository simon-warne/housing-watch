#!/usr/bin/env bash
# Replace the `data` branch with one commit holding DIR's history.sqlite and state.db.enc
# (Simon, 2026-11-26: identical pushes every run, so push timing reveals nothing about alerts).
# Pushes only if the runner wrote DIR/ready, and only over the commit fetch-data.sh saw.
set -euo pipefail
dir=${1:?usage: push-data.sh DIR}
remote=${REMOTE:-origin}
if [ ! -f "$dir/ready" ]; then echo "not ready: nothing pushed"; exit 0; fi
[ -f "$dir/.base" ] || { echo "no .base from fetch-data.sh"; exit 1; }
h=$(git hash-object -w "$dir/history.sqlite")
s=$(git hash-object -w "$dir/state.db.enc")
tree=$(printf '100644 blob %s\thistory.sqlite\n100644 blob %s\tstate.db.enc\n' "$h" "$s" | git mktree)
export GIT_AUTHOR_NAME=housing-watch GIT_COMMITTER_NAME=housing-watch
export GIT_AUTHOR_EMAIL=housing-watch@users.noreply.github.com
export GIT_COMMITTER_EMAIL=$GIT_AUTHOR_EMAIL
commit=$(git commit-tree "$tree" -m "data")
git push -q --force-with-lease="refs/heads/data:$(cat "$dir/.base")" "$remote" "$commit:refs/heads/data"
echo "data pushed"
