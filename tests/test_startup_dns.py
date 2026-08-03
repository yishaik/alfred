"""A resolver that is not up yet must be waited out, not treated as a crash.

The 2026-08-01 04:47 boot brought the bridge up before DNS worked; PTB's
get_me() raised, the process died in ~1s, and the supervisor's ladder left
Alfred offline for 47 minutes. main._await_dns holds the process instead.
"""

import socket

import pytest

from tgbridge import main


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    """Keep the backoff logic exercised without spending its wall-clock."""
    slept = []
    monkeypatch.setattr(main.time, "sleep", lambda s: slept.append(s))
    return slept


def test_returns_immediately_when_dns_works(monkeypatch, no_real_sleep):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [("ok",)])
    assert main._await_dns() is True
    assert no_real_sleep == []


def test_waits_then_succeeds_when_dns_comes_back(monkeypatch, no_real_sleep):
    calls = {"n": 0}

    def flaky(*_a, **_k):
        calls["n"] += 1
        if calls["n"] < 4:
            raise socket.gaierror("Temporary failure in name resolution")
        return [("ok",)]

    monkeypatch.setattr(socket, "getaddrinfo", flaky)
    assert main._await_dns() is True
    assert calls["n"] == 4
    assert no_real_sleep == [2.0, 4.0, 8.0]      # exponential, no busy spin


def test_backoff_is_capped(monkeypatch, no_real_sleep):
    def dead(*_a, **_k):
        raise socket.gaierror("Temporary failure in name resolution")

    monkeypatch.setattr(socket, "getaddrinfo", dead)
    main._await_dns(budget=600.0)
    assert max(no_real_sleep) == 30.0            # never sleeps longer than 30s


def test_gives_up_once_the_budget_is_spent(monkeypatch, no_real_sleep):
    """A genuinely broken network must still surface as a non-zero exit rather
    than a bridge that looks alive but can never reach Telegram."""
    def dead(*_a, **_k):
        raise socket.gaierror("Temporary failure in name resolution")

    monkeypatch.setattr(socket, "getaddrinfo", dead)
    assert main._await_dns(budget=60.0) is False
    assert sum(no_real_sleep) >= 60.0
