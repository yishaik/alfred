"""Phase-1 security hardening: child-env scrub, guard parsing, send_file gap.

All values here are synthetic. See
.local/state/alfred-egm-review-2026-11-01/security-fix-spec.md.
"""
import pytest

from tgbridge import config
from tgbridge.guards import is_forbidden


# ---- S1: one child-env contract -------------------------------------------

def test_scrub_blanks_secret_shaped_names(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-synthetic")
    monkeypatch.setenv("SOME_BOT_TOKEN", "123:synthetic")
    monkeypatch.setenv("MY_PASSWORD", "hunter2")
    env = config.scrubbed_child_env()
    assert env["OPENAI_API_KEY"] == ""
    assert env["SOME_BOT_TOKEN"] == ""
    assert env["MY_PASSWORD"] == ""


def test_scrub_covers_dotenv_key_the_regex_misses(monkeypatch):
    # BRIDGE_PEER_LEGACY is a real .env key that matches no secret-name pattern.
    monkeypatch.setenv("BRIDGE_PEER_LEGACY", "synthetic-peer-secret")
    monkeypatch.setattr(config, "DOTENV_KEYS", {"BRIDGE_PEER_LEGACY"})
    env = config.scrubbed_child_env()
    assert env["BRIDGE_PEER_LEGACY"] == ""


def test_scrub_keeps_model_auth_and_plain_vars(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-synthetic")
    monkeypatch.setenv("CLAUDE_CODE_SOMETHING", "x")
    monkeypatch.setattr(config, "DOTENV_KEYS", set())
    env = config.scrubbed_child_env()
    # Kept means: not present as a blank override.
    assert "ANTHROPIC_API_KEY" not in env
    assert "CLAUDE_CODE_SOMETHING" not in env
    assert "PATH" not in env


def test_scrub_keeps_nonsecret_dotenv_config(monkeypatch):
    # A plain .env setting (a path) must stay live in the child; only secrets go.
    monkeypatch.setenv("BRIDGE_WORKDIR", "/home/ubuntu/git")
    monkeypatch.setattr(config, "DOTENV_KEYS", {"BRIDGE_WORKDIR"})
    env = config.scrubbed_child_env()
    assert "BRIDGE_WORKDIR" not in env


# ---- S2: guard path coverage + precise secretbox exemption ----------------

@pytest.mark.parametrize("cmd", [
    "cat /home/ubuntu/alfred/vault.enc",
    "cp /home/ubuntu/.secretbox/master.key /tmp/x",
    "head ~/alfred/.env.local",
    "head ~/alfred/.env.local.bak",
    "grep token /home/ubuntu/alfred/vault.json",
    # unlisted readers must not matter — naming the file is enough
    "perl -ne 'print' /home/ubuntu/alfred/.env",
    "dd if=/home/ubuntu/alfred/.env of=/tmp/x",
    "cut -c1- /home/ubuntu/alfred/.env",
    # redirection right after the name
    "cat /home/ubuntu/alfred/.env>/tmp/x",
    # quote-splitting the basename collapses back under shlex
    "cat /home/ubuntu/alfred/.e'n'v",
    # any file under the secret dir, whatever the basename
    "cat /home/ubuntu/.secretbox/creds.ini",
])
def test_forbidden_blocks_naming_a_secret(cmd):
    assert is_forbidden("Bash", {"command": cmd}) == "names a secret file"


def test_forbidden_allows_legit_secretbox_run():
    # --run names a KEY, not a file, so nothing to block.
    cmd = "python3 /home/ubuntu/alfred/secretbox.py --run OPENAI_API_KEY -- node app.js"
    assert is_forbidden("Bash", {"command": cmd}) is None


def test_forbidden_blocks_secretbox_dashdash_reader():
    # The regression the reviewer caught: `--` child reading .env, no shell chain.
    cmd = "python3 /home/ubuntu/alfred/secretbox.py --run K -- cat /home/ubuntu/alfred/.env"
    assert is_forbidden("Bash", {"command": cmd}) == "names a secret file"


def test_forbidden_comment_cannot_exempt_a_real_read():
    cmd = "cat /home/ubuntu/alfred/vault.enc  # secretbox.py"
    assert is_forbidden("Bash", {"command": cmd}) == "names a secret file"


def test_forbidden_allows_ordinary_command():
    assert is_forbidden("Bash", {"command": "ls -la /home/ubuntu/git"}) is None


# ---- send_file path gap ----------------------------------------------------

@pytest.mark.parametrize("tool", ["send_file", "mcp__bridge__send_file"])
@pytest.mark.parametrize("path", [
    "/home/ubuntu/alfred/vault.enc",
    "/home/ubuntu/alfred/.env.local.bak",
    "/home/ubuntu/.secretbox/creds.ini",
])
def test_send_file_blocks_secret_path(tool, path):
    assert is_forbidden(tool, {"path": path}) == "sends a secret file"


@pytest.mark.parametrize("tool", ["send_file", "mcp__bridge__send_file"])
def test_send_file_allows_ordinary_path(tool):
    assert is_forbidden(tool, {"path": "/home/ubuntu/report.pdf"}) is None
