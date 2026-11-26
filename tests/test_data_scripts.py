"""fetch-data.sh / push-data.sh against a local bare remote (plan M4). Needs git."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


def sh(*args, cwd, env=None, check=True):
    return subprocess.run(
        args,
        cwd=cwd,
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        check=check,
    )


@pytest.fixture
def repo(tmp_path):
    remote = tmp_path / "remote.git"
    sh("git", "init", "-q", "--bare", str(remote), cwd=tmp_path)
    work = tmp_path / "work"
    work.mkdir()
    sh("git", "init", "-q", cwd=work)
    sh("git", "remote", "add", "origin", str(remote), cwd=work)
    return work


def fetch(work, out):
    return sh(
        str(ROOT / "scripts/fetch-data.sh"),
        "data",
        cwd=work,
        env={"GITHUB_OUTPUT": str(out)},
        check=False,
    )


def push(work):
    return sh(str(ROOT / "scripts/push-data.sh"), "data", cwd=work, check=False)


def write(work, hist=b"H1", state=b"S1", ready=True):
    d = work / "data"
    (d / "history.sqlite").write_bytes(hist)
    (d / "state.db.enc").write_bytes(state)
    if ready:
        (d / "ready").touch()


def remote_files(work):
    sh("git", "fetch", "-q", "origin", "data", cwd=work)
    names = sh("git", "ls-tree", "--name-only", "FETCH_HEAD", cwd=work).stdout.split()
    return {n: sh("git", "show", f"FETCH_HEAD:{n}", cwd=work).stdout for n in names}


def test_first_run_then_replace(repo, tmp_path):
    out = tmp_path / "out"
    r = fetch(repo, out)
    assert r.returncode == 0 and "init=1" in out.read_text()
    write(repo)
    assert push(repo).returncode == 0
    assert remote_files(repo) == {"history.sqlite": "H1", "state.db.enc": "S1"}

    shutil.rmtree(repo / "data")
    out.write_text("")
    assert fetch(repo, out).returncode == 0
    assert "init=\n" in out.read_text() and "init=1" not in out.read_text()
    assert (repo / "data/history.sqlite").read_bytes() == b"H1"
    write(repo, b"H2", b"S2")
    assert push(repo).returncode == 0
    assert remote_files(repo) == {"history.sqlite": "H2", "state.db.enc": "S2"}
    # one commit, no history of the data
    log = sh("git", "rev-list", "--count", "FETCH_HEAD", cwd=repo).stdout.strip()
    assert log == "1"


def test_not_ready_pushes_nothing(repo, tmp_path):
    fetch(repo, tmp_path / "out")
    write(repo, ready=False)
    r = push(repo)
    assert r.returncode == 0 and "nothing pushed" in r.stdout
    assert sh("git", "ls-remote", "--heads", "origin", "data", cwd=repo).stdout == ""


def test_push_refuses_if_data_moved_since_fetch(repo, tmp_path):
    fetch(repo, tmp_path / "out")
    write(repo)
    push(repo)
    shutil.rmtree(repo / "data")
    fetch(repo, tmp_path / "out")  # .base = first commit
    other = tmp_path / "other"
    sh("git", "clone", "-q", str(tmp_path / "remote.git"), str(other), cwd=tmp_path)
    (other / "data").mkdir()
    (other / "data/.base").write_text(sh("git", "rev-parse", "origin/data", cwd=other).stdout)
    write(other, b"X", b"Y")
    assert push(other).returncode == 0  # someone else replaced it
    write(repo, b"H2", b"S2")
    assert push(repo).returncode != 0  # lease fails: never overwrite unseen data
    assert remote_files(repo)["history.sqlite"] == "X"


def test_fetch_fails_loudly_without_a_remote(tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    sh("git", "init", "-q", cwd=work)
    sh("git", "remote", "add", "origin", str(tmp_path / "missing.git"), cwd=work)
    out = tmp_path / "out"
    r = fetch(work, out)
    assert r.returncode != 0
    assert not out.exists() or "init=1" not in out.read_text()
