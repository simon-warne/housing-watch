"""scripts/alert-failure.sh: one ntfy alert per failure streak of the watch workflow
(Simon, 2026-11-28). `gh` and `curl` are fakes on PATH; the topic is made up."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "scripts/alert-failure.sh"
TOPIC = "made-up-topic-123"
RUN_ID = 500
RUN_URL = "https://github.com/owner/housing-watch/actions/runs/500"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def run(tmp_path, runs, *, gh_fails=False, topic=TOPIC):
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    (tmp_path / "curl.args").unlink(missing_ok=True)
    (tmp_path / "runs.json").write_text(json.dumps(runs))
    gh = "exit 1" if gh_fails else f'echo "$@" > {tmp_path}/gh.args; cat {tmp_path}/runs.json'
    (bin_ / "gh").write_text(f"#!/usr/bin/env bash\n{gh}\n")
    (bin_ / "curl").write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "$@" > {tmp_path}/curl.args\n')
    for f in bin_.iterdir():
        f.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{bin_}:{os.environ['PATH']}",
        "REPO": "owner/housing-watch",
        "RUN_ID": str(RUN_ID),
        "RUN_URL": RUN_URL,
    }
    env.pop("NTFY_TOPIC", None)
    if topic is not None:
        env["NTFY_TOPIC"] = topic
    p = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)
    curl = tmp_path / "curl.args"
    return p, (curl.read_text().splitlines() if curl.exists() else None)


def r(i, conclusion, status="completed", title="watch"):
    return {"databaseId": i, "status": status, "conclusion": conclusion, "displayTitle": title}


def test_first_failure_after_a_success_alerts(tmp_path):
    p, curl = run(tmp_path, [r(RUN_ID, ""), r(499, "success"), r(498, "failure")])
    assert p.returncode == 0, p.stderr
    assert curl is not None
    assert "Title: housing-watch: run failed" in curl
    assert f"Click: {RUN_URL}" in curl
    body = curl[curl.index("-d") + 1]
    assert body.startswith("housing-watch: run failed") and RUN_URL in body
    assert curl[-1] == f"https://ntfy.sh/{TOPIC}"
    assert TOPIC not in p.stdout + p.stderr  # public log


def test_failure_after_a_failure_is_already_alerted(tmp_path):
    p, curl = run(tmp_path, [r(RUN_ID, ""), r(499, "failure"), r(498, "success")])
    assert p.returncode == 0
    assert curl is None
    assert "already alerted" in p.stdout


def test_previous_is_the_latest_completed_run_before_this_one(tmp_path):
    # A queued run after this one, and this run itself, never count.
    runs = [
        r(501, "", "queued"),
        r(RUN_ID, "", "in_progress"),
        r(497, "failure"),
        r(499, "success"),
    ]
    _, curl = run(tmp_path, runs)
    assert curl is not None


def test_no_earlier_run_alerts(tmp_path):
    _, curl = run(tmp_path, [r(RUN_ID, "", "in_progress")])
    assert curl is not None


def test_gh_error_still_alerts(tmp_path):
    # Better a second alert than none.
    p, curl = run(tmp_path, [], gh_fails=True)
    assert p.returncode == 0 and curl is not None


def test_asks_only_for_watch_runs_of_this_repo(tmp_path):
    run(tmp_path, [r(499, "success")])
    args = (tmp_path / "gh.args").read_text()
    assert "run list" in args and "owner/housing-watch" in args and "watch.yml" in args
    assert "--branch main" in args  # only main's runs make a streak (Simon, 2026-11-28)


def test_missing_topic_fails(tmp_path):
    p, curl = run(tmp_path, [r(499, "success")], topic=None)
    assert p.returncode != 0 and curl is None


def test_cancelled_or_skipped_runs_do_not_split_a_streak(tmp_path):
    # A run waiting in the concurrency group is cancelled when a newer one arrives.
    runs = [r(RUN_ID, ""), r(499, "cancelled"), r(498, "skipped"), r(497, "failure")]
    p, curl = run(tmp_path, runs)
    assert curl is None and "already alerted" in p.stdout


def test_test_alert_runs_do_not_count(tmp_path):
    runs = [r(RUN_ID, ""), r(499, "success", title="watch test alert"), r(498, "failure")]
    _, curl = run(tmp_path, runs)
    assert curl is None
    runs = [r(RUN_ID, ""), r(499, "failure", title="watch test alert"), r(498, "success")]
    _, curl = run(tmp_path, runs)
    assert curl is not None
