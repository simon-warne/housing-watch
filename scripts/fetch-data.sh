#!/usr/bin/env bash
# Copy history.sqlite and state.db.enc from the `data` branch into DIR (plan M4).
# Only a missing branch means "first run" (step output init=1, the runner then needs --init);
# any other failure stops here, so a glitch never starts from empty and overwrites the data.
# DIR/.base records the fetched commit, for push-data.sh's --force-with-lease.
set -euo pipefail
dir=${1:?usage: fetch-data.sh DIR}
remote=${REMOTE:-origin}
mkdir -p "$dir"
set +e
git ls-remote --exit-code --heads "$remote" data >/dev/null
rc=$?
set -e
if [ "$rc" -eq 2 ]; then
  : > "$dir/.base"
  echo "init=1" >> "${GITHUB_OUTPUT:-/dev/null}"
  echo "no data branch yet: first run"
  exit 0
fi
[ "$rc" -eq 0 ] || { echo "ls-remote failed ($rc)"; exit 1; }
git fetch -q --depth=1 --no-tags "$remote" data
git rev-parse FETCH_HEAD > "$dir/.base"
for f in history.sqlite state.db.enc; do git show "FETCH_HEAD:$f" > "$dir/$f"; done
echo "init=" >> "${GITHUB_OUTPUT:-/dev/null}"
echo "data fetched"
