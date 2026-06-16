"""Passive watchers — react to changes in the world without being asked (#6).

Point Alfred at a file, a folder, or a git repo and it polls them in the
background; when one changes, it feeds the agent a turn describing the change
through the proactive channel (#5), so the agent decides whether the change is
worth a word or stays silent. This is the event-driven sibling of the idle
check-in — same "speak only if it matters" discipline.

Detection is by cheap state fingerprints (no file contents are read):
  * file    — mtime:size
  * dir     — a hash of the immediate children's (name, mtime, size)
  * gitrepo — HEAD commit + a hash of `git status --porcelain`

The fingerprint helpers (dir signature, kind detection) are pure and
unit-tested; the polling loop lives on the manager.
"""

import hashlib
import os
import subprocess
from dataclasses import dataclass

KINDS = ("file", "dir", "gitrepo")


def dir_signature(entries: list) -> str:
    """Pure: a stable short hash of a directory listing. `entries` is a list of
    (name, mtime, size). Order-independent; changes if any child is added,
    removed, or modified."""
    norm = sorted(f"{n}:{int(m)}:{s}" for n, m, s in entries)
    return hashlib.sha1("\n".join(norm).encode("utf-8")).hexdigest()[:16]


def detect_kind(path: str) -> str | None:
    """Classify a watch target, or None if it doesn't exist."""
    if os.path.isdir(os.path.join(path, ".git")):
        return "gitrepo"
    if os.path.isdir(path):
        return "dir"
    if os.path.isfile(path):
        return "file"
    return None


def compute_state(path: str, kind: str) -> str | None:
    """A fingerprint of the target's current state, or None if unreadable.
    Comparing two fingerprints tells us whether it changed."""
    try:
        if kind == "file":
            st = os.stat(path)
            return f"{int(st.st_mtime)}:{st.st_size}"
        if kind == "dir":
            entries = []
            with os.scandir(path) as it:
                for e in it:
                    try:
                        st = e.stat()
                        entries.append((e.name, st.st_mtime, st.st_size))
                    except OSError:
                        continue
            return dir_signature(entries)
        if kind == "gitrepo":
            head = subprocess.run(
                ["git", "-C", path, "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=15)
            status = subprocess.run(
                ["git", "-C", path, "status", "--porcelain"],
                capture_output=True, text=True, timeout=15)
            dirty = hashlib.sha1(status.stdout.encode()).hexdigest()[:8]
            return f"{head.stdout.strip()[:12]}:{dirty}"
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def watch_prompt(label: str, kind: str) -> str:
    """The proactive turn fed to the agent when a watcher trips. Reuses the
    silence sentinel from the proactive channel, so the agent stays quiet when
    the change isn't worth interrupting for."""
    from .proactive import SENTINEL
    return (f"[watcher — {kind} '{label}' just changed; this turn is automatic]\n"
            "Take a quick look if it's easy to (e.g. git log / status for a "
            "repo). If there's something the user would want to know — a new "
            "commit, a failing build, a notable edit — say it in one or two "
            "lines. If it's routine or not worth interrupting for, reply with "
            f"exactly: {SENTINEL}")


@dataclass
class Watcher:
    path: str
    kind: str
    label: str = ""
    last_state: str = ""

    def to_dict(self) -> dict:
        return {"path": self.path, "kind": self.kind, "label": self.label,
                "last_state": self.last_state}

    @classmethod
    def from_dict(cls, d: dict) -> "Watcher":
        return cls(path=d.get("path", ""), kind=d.get("kind", "file"),
                   label=d.get("label", ""), last_state=d.get("last_state", ""))
