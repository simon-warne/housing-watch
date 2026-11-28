"""The watch workflow's must-always rules, as tests (plan M4, code-setup §7)."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
WF = yaml.safe_load((ROOT / ".github/workflows/watch.yml").read_text(encoding="utf-8"))
ON = WF.get("on", WF.get(True))  # PyYAML reads a bare `on:` key as True
STEPS = WF["jobs"]["watch"]["steps"]
SECRET = re.compile(r"\$\{\{\s*secrets\.(\w+)\s*\}\}")
ALLOWED_SECRETS = {
    "NTFY_TOPIC",
    "STATE_KEY",
    "QUEUE_DAYS",
    "QUEUE_DAYS_UNTIL",
    "BF_QUEUE_SINCE",
    "FV_POINTS_AT",
}


def _step(name):
    return next(s for s in STEPS if s.get("name") == name)


def test_schedule_is_every_30_min_at_odd_minutes():
    crons = [c["cron"] for c in ON["schedule"]]
    assert crons == ["7,37 * * * *"]
    minutes = [int(m) for m in crons[0].split()[0].split(",")]
    assert 0 not in minutes and all(m % 5 for m in minutes)  # not :00, :05, :30 …
    assert sorted(minutes)[1] - sorted(minutes)[0] == 30  # SSSB at most every 30 min


def test_runs_never_overlap():
    assert WF["concurrency"] == {"group": "watch", "cancel-in-progress": False}


def test_permissions_are_minimal():
    assert WF["permissions"] == {"contents": "write", "actions": "write"}
    assert "permissions" not in WF["jobs"]["watch"]


def test_secrets_only_through_step_env():
    assert "env" not in WF and "env" not in WF["jobs"]["watch"]
    seen = set()
    for s in STEPS:
        for field in ("run", "with", "if"):
            assert not SECRET.search(str(s.get(field, ""))), (s.get("name"), field)
        for k, v in (s.get("env") or {}).items():
            m = SECRET.fullmatch(str(v).strip())
            if m:
                assert m.group(1) == k  # same name, so nothing is renamed or combined
                seen.add(k)
    assert seen == ALLOWED_SECRETS


def test_no_secret_is_echoed_or_traced():
    for s in STEPS:
        run = s.get("run", "")
        assert "set -x" not in run and "printenv" not in run and "env |" not in run
        for name in ALLOWED_SECRETS:
            assert f"${name}" not in run and f"${{{name}}}" not in run, (s.get("name"), name)


def test_booked_ids_is_a_variable_not_in_logs():
    assert _step("Watch")["env"]["BOOKED_IDS"] == "${{ vars.BOOKED_IDS }}"


def test_push_runs_after_failures_but_not_for_test_alerts():
    push = _step("Push data branch")
    assert "always()" in push["if"] and "!inputs.test_alert" in push["if"]
    assert push["run"] == "scripts/push-data.sh data"


def test_keep_alive_on_every_watch_run_and_never_fails_the_run():
    k = _step("Keep the schedule alive")
    # Runs come mostly from an external workflow_dispatch trigger (Simon, 2026-11-27): GitHub's
    # schedule rarely fires, so a schedule-only keep-alive would miss its weekly window.
    assert "always()" in k["if"] and "!inputs.test_alert" in k["if"]
    assert "event_name" not in k["if"]
    assert k["continue-on-error"] is True
    assert "workflows/watch.yml/enable" in k["run"]
    # Weekly only (Simon, 2026-11-27): re-enabling every run may re-register the schedule.
    assert '"703"' in k["run"] and "exit 0" in k["run"]


def test_backup_is_weekly_and_keeps_state_encrypted():
    b = _step("Weekly backup")
    paths = b["with"]["path"].split()
    assert paths == ["data/history.sqlite", "data/state.db.enc"]
    assert b["with"]["retention-days"] == 90
    assert '"703"' in _step("Weekly backup due?")["run"]  # Sunday, 03 UTC


def test_watch_step_gets_the_bf_queue_date():
    assert _step("Watch")["env"]["BF_QUEUE_SINCE"] == "${{ secrets.BF_QUEUE_SINCE }}"


def test_watch_step_gets_the_fv_points():
    env = _step("Watch")["env"]
    assert env["FV_POINTS_AT"] == "${{ secrets.FV_POINTS_AT }}"
    assert "FV_POINTS" not in env and "FV_POINTS_ON" not in env


def test_failure_alert_is_the_last_step_once_per_failure():
    # One ntfy alert per failure streak (Simon, 2026-11-28): a re-run attempt never alerts,
    # and the script skips a run whose previous watch run failed too.
    a = STEPS[-1]
    assert a["name"] == "Alert on failure"
    assert "failure()" in a["if"] and "github.run_attempt == 1" in a["if"]
    assert "always()" not in a["if"]
    assert a["env"]["NTFY_TOPIC"] == "${{ secrets.NTFY_TOPIC }}"
    assert a["env"]["RUN_ID"] == "${{ github.run_id }}"
    assert a["env"]["RUN_URL"].endswith("/actions/runs/${{ github.run_id }}")
    assert a["run"] == "scripts/alert-failure.sh"
