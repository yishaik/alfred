from __future__ import annotations

import pytest

from cloud.fly_machines import WorkerJob
from cloud.security import PolicyError, production_approval, redact, resolve_under, validate_repo


def test_validate_repo_accepts_owner_name(monkeypatch):
    monkeypatch.delenv("ALFRED_ALLOWED_REPOS", raising=False)
    assert validate_repo("yishaik/alfred") == "yishaik/alfred"


@pytest.mark.parametrize("value", ["alfred", "../x/y", "x/../../y", "x/y z", "/x/y"])
def test_validate_repo_rejects_unsafe(value, monkeypatch):
    monkeypatch.delenv("ALFRED_ALLOWED_REPOS", raising=False)
    with pytest.raises(PolicyError):
        validate_repo(value)


def test_repo_allowlist(monkeypatch):
    monkeypatch.setenv("ALFRED_ALLOWED_REPOS", "yishaik/alfred,yishaik/second")
    assert validate_repo("yishaik/alfred") == "yishaik/alfred"
    with pytest.raises(PolicyError):
        validate_repo("someone/else")


def test_resolve_under_rejects_escape(tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    assert resolve_under(root, "repo") == (root / "repo").resolve()
    with pytest.raises(PolicyError):
        resolve_under(root, "../secret")


def test_production_requires_flag_and_exact_token(monkeypatch):
    monkeypatch.setenv("ALFRED_ALLOW_PRODUCTION_DEPLOY", "0")
    with pytest.raises(PolicyError):
        production_approval("vercel", "yishaik/alfred", "vercel:yishaik/alfred")
    monkeypatch.setenv("ALFRED_ALLOW_PRODUCTION_DEPLOY", "1")
    with pytest.raises(PolicyError):
        production_approval("vercel", "yishaik/alfred", "yes")
    production_approval("vercel", "yishaik/alfred", "vercel:yishaik/alfred")


def test_redaction(monkeypatch):
    monkeypatch.setenv("EXAMPLE_API_KEY", "12345678-secret-value")
    assert "12345678-secret-value" not in redact("x=12345678-secret-value")


def test_worker_job_payload_is_bounded(monkeypatch):
    monkeypatch.delenv("ALFRED_ALLOWED_REPOS", raising=False)
    job = WorkerJob("yishaik/alfred", "agent/test", "review this", "abc123")
    assert job.payload()
    with pytest.raises(PolicyError):
        WorkerJob("yishaik/alfred", "agent/test", "x" * 20001, "abc123").payload()
