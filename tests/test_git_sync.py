"""Tests for Lambda git sync helpers."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import lambda_handlers.git_sync as git_sync


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    )


def _init_bare_and_clone(tmp_path: Path) -> tuple[Path, Path]:
    bare = tmp_path / "remote.git"
    clone = tmp_path / "work"
    _git(tmp_path, "init", "--bare", "-b", "main", str(bare))
    _git(tmp_path, "clone", str(bare), str(clone))
    _git(clone, "config", "user.name", "test")
    _git(clone, "config", "user.email", "test@example.com")
    (clone / "README.md").write_text("base\n")
    _git(clone, "add", "README.md")
    _git(clone, "commit", "-m", "init")
    _git(clone, "push", "-u", "origin", "main")
    return bare, clone


def _clone_other(tmp_path: Path, bare: Path, name: str = "other") -> Path:
    other = tmp_path / name
    _git(tmp_path, "clone", "-b", "main", str(bare), str(other))
    _git(other, "config", "user.name", name)
    _git(other, "config", "user.email", f"{name}@example.com")
    return other


def test_redact_strips_github_pat():
    leaked = (
        "git pull --rebase "
        "https://x-access-token:github_pat_11AHOV26Y099NsPKaL1R0g_SECRET"
        "@github.com/owner/repo.git main"
    )
    redacted = git_sync._redact(leaked)
    assert "11AHOV26Y099" not in redacted
    assert "SECRET" not in redacted
    assert "***" in redacted


def test_commit_and_push_rebases_over_remote_commit(tmp_path, monkeypatch):
    bare, workspace = _init_bare_and_clone(tmp_path)
    monkeypatch.setattr(git_sync, "WORKSPACE", workspace)
    monkeypatch.setattr(git_sync, "PUSH_MAX_ATTEMPTS", 3)
    monkeypatch.setattr(git_sync, "FETCH_DEPTH", 30)

    other = _clone_other(tmp_path, bare)
    (other / "data").mkdir()
    (other / "data" / "remote_only.txt").write_text("from-remote\n")
    _git(other, "add", "data/remote_only.txt")
    _git(other, "commit", "-m", "remote change")
    _git(other, "push", "origin", "main")

    (workspace / "data").mkdir(exist_ok=True)
    target = "data/analysis_out.json"
    (workspace / target).write_text('{"ok": true}\n')

    def configure_local(_pat: str, _repo: str) -> str:
        _git(workspace, "remote", "set-url", "origin", str(bare))
        return str(bare)

    monkeypatch.setattr(git_sync, "_configure_remote", configure_local)

    committed = git_sync.commit_and_push(
        [target],
        "chore(data): test sync",
        github_pat="github_pat_dummy",
        git_repo="owner/repo",
        branch="main",
    )
    assert committed is True

    verify = tmp_path / "verify"
    _git(tmp_path, "clone", "-b", "main", str(bare), str(verify))
    assert (verify / "data" / "remote_only.txt").exists()
    assert (verify / target).read_text() == '{"ok": true}\n'


def test_commit_and_push_reapplies_on_conflict(tmp_path, monkeypatch):
    bare, workspace = _init_bare_and_clone(tmp_path)
    monkeypatch.setattr(git_sync, "WORKSPACE", workspace)
    monkeypatch.setattr(git_sync, "PUSH_MAX_ATTEMPTS", 3)

    shared = "data/analysis/trade_history.json"
    (workspace / "data" / "analysis").mkdir(parents=True)
    (workspace / shared).write_text('{"v": 1}\n')
    _git(workspace, "add", shared)
    _git(workspace, "commit", "-m", "seed history")
    _git(workspace, "push", "origin", "main")

    other = _clone_other(tmp_path, bare)
    (other / shared).parent.mkdir(parents=True, exist_ok=True)
    (other / shared).write_text('{"v": "remote"}\n')
    _git(other, "add", shared)
    _git(other, "commit", "-m", "remote history")
    _git(other, "push", "origin", "main")

    # Diverged local edit (workspace still on seed tip until commit_and_push fetches).
    (workspace / shared).write_text('{"v": "local"}\n')

    def configure_local(_pat: str, _repo: str) -> str:
        _git(workspace, "remote", "set-url", "origin", str(bare))
        return str(bare)

    monkeypatch.setattr(git_sync, "_configure_remote", configure_local)

    (other / shared).write_text('{"v": "remote2"}\n')
    _git(other, "add", shared)
    _git(other, "commit", "-m", "remote2")
    _git(other, "push", "origin", "main")

    committed = git_sync.commit_and_push(
        [shared],
        "chore(data): sync conflict",
        github_pat="github_pat_dummy",
        git_repo="owner/repo",
        branch="main",
    )
    assert committed is True

    verify = tmp_path / "verify2"
    _git(tmp_path, "clone", "-b", "main", str(bare), str(verify))
    assert (verify / shared).read_text() == '{"v": "local"}\n'


def test_commit_and_push_with_unstaged_extra_file(tmp_path, monkeypatch):
    """Unstaged tracked edits (e.g. denylist) must not block rebase."""
    bare, workspace = _init_bare_and_clone(tmp_path)
    monkeypatch.setattr(git_sync, "WORKSPACE", workspace)
    monkeypatch.setattr(git_sync, "PUSH_MAX_ATTEMPTS", 3)

    target = "data/analysis/trade_history.json"
    extra = "data/analysis/timezone_skip_denylist.json"
    (workspace / "data" / "analysis").mkdir(parents=True)
    (workspace / target).write_text('{"v": 1}\n')
    (workspace / extra).write_text('{"timezones": []}\n')
    _git(workspace, "add", target, extra)
    _git(workspace, "commit", "-m", "seed")
    _git(workspace, "push", "origin", "main")

    other = _clone_other(tmp_path, bare)
    (other / "data" / "analysis" / "remote.txt").write_text("remote\n")
    _git(other, "add", "data/analysis/remote.txt")
    _git(other, "commit", "-m", "remote")
    _git(other, "push", "origin", "main")

    (workspace / target).write_text('{"v": "local"}\n')
    # Simulate sync writing denylist but only staging trade_history (old bug).
    (workspace / extra).write_text('{"timezones": ["America/Bogota"]}\n')

    def configure_local(_pat: str, _repo: str) -> str:
        _git(workspace, "remote", "set-url", "origin", str(bare))
        return str(bare)

    monkeypatch.setattr(git_sync, "_configure_remote", configure_local)

    committed = git_sync.commit_and_push(
        [target],
        "chore(data): sync history only",
        github_pat="github_pat_dummy",
        git_repo="owner/repo",
        branch="main",
    )
    assert committed is True

    verify = tmp_path / "verify3"
    _git(tmp_path, "clone", "-b", "main", str(bare), str(verify))
    assert (verify / target).read_text() == '{"v": "local"}\n'
    assert (verify / "data" / "analysis" / "remote.txt").exists()


def test_run_never_raises_with_raw_pat_in_message(tmp_path):
    with pytest.raises(RuntimeError) as excinfo:
        git_sync._run(
            [
                "git",
                "ls-remote",
                "https://x-access-token:github_pat_SECRETtokenVALUE@github.com/no/such.git",
            ],
            cwd=tmp_path,
        )
    msg = str(excinfo.value)
    assert "github_pat_SECRETtokenVALUE" not in msg
    assert "***" in msg
