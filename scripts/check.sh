#!/usr/bin/env bash
# Full local check: same as CI; gitleaks over the full history if installed.
set -uo pipefail
cd "$(dirname "$0")/.."
uv run ruff format --check . && uv run ruff check . || exit 1
uv run pytest -q; rc=$?
[ $rc -eq 5 ] && { echo "no tests yet"; rc=0; }
command -v gitleaks >/dev/null && { gitleaks git --no-banner --redact --log-level warn . || exit 1; }
exit $rc
