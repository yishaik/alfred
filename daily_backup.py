#!/usr/bin/env python3
"""Standalone daily state backup.

Runs the bridge's own AgentManager.backup_state() rather than reimplementing it,
so the zip contents and the offsite mirror can never drift from what /backup
does. backup_state touches only the filesystem and env, so a stub app with a
.bot attribute is enough — no Telegram connection is opened.

Reports the outcome through opsnotify.sh (@AlfredOps2bot). Intended for a
systemd timer; safe to run twice a day — it no-ops if today's zip exists.
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# Load alfred's .env: BRIDGE_BACKUP_REPO decides where the offsite mirror goes.
env_file = ROOT / ".env"
if env_file.is_file():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def notify(text: str) -> None:
    try:
        subprocess.run([str(ROOT / "opsnotify.sh"), text], timeout=30,
                       capture_output=True)
    except Exception:
        pass


class _StubApp:
    bot = None


def main() -> int:
    from tgbridge.manager import AgentManager, BACKUP_DIR

    before = {p.name for p in BACKUP_DIR.glob("state-*.zip")} if BACKUP_DIR.is_dir() else set()

    m = AgentManager(_StubApp())
    m.backup_state()

    after = {p.name for p in BACKUP_DIR.glob("state-*.zip")} if BACKUP_DIR.is_dir() else set()
    new = sorted(after - before)

    if not new:
        # Either today's zip already existed, or backup_state swallowed a failure.
        if after:
            print("backup: today's zip already present — nothing to do")
            return 0
        notify("⚠️ גיבוי אלפרד נכשל — לא נוצר קובץ ולא קיים גיבוי קודם.")
        return 1

    z = BACKUP_DIR / new[-1]
    size_kb = z.stat().st_size // 1024
    kept = len(after)
    # Verify the mirror actually landed rather than trusting that the env var is
    # set — the offsite copy failing silently is the whole failure mode here.
    repo = os.environ.get("BRIDGE_BACKUP_REPO", "").strip()
    if not repo:
        mirror = "⚠️ מראה offsite: לא מוגדר"
    elif (Path(repo) / z.name).is_file():
        mirror = "מראה offsite: ✅"
    else:
        mirror = f"⚠️ מראה offsite נכשל ({repo})"
    notify(f"💾 גיבוי אלפרד — {z.name} ({size_kb} KB)\nשומר {kept} גיבויים · {mirror}")
    print(f"backup: {z.name} ({size_kb} KB), {kept} kept")
    return 0


if __name__ == "__main__":
    sys.exit(main())
