"""Auto-escalation — catch a brewing problem before it breaks things (#8).

The daily health report is a once-a-day snapshot; this watches the same vital
signs every few minutes and pings the moment one crosses a line — low disk, a
session whose queue is backing up (likely stuck), or a run of crashes. Each
alert is edge-triggered: it fires once when the condition appears and stays
quiet until it clears and recurs, so a persistent problem never spams.

assess() is pure (snapshot in, alerts out) and unit-tested; the monitor loop
that gathers the snapshot lives on the manager.
"""

# Thresholds — set a touch earlier than the health report's hard limits so a
# warning lands while there's still room to act.
SYS_DISK_WARN_GB = 3.0
PROJ_DISK_WARN_GB = 5.0
QUEUE_WARN = 10            # messages backed up in one session => probably stuck
CRASH_WARN = 3            # session crashes within the crash window => unstable
CRASH_WINDOW_S = 3600.0


def assess(snap: dict) -> list:
    """Return [(key, message)] for every tripped signal. `key` lets the caller
    de-duplicate so each condition alerts once until it clears."""
    out = []
    sys_free = snap.get("sys_free_gb")
    if sys_free is not None and sys_free < SYS_DISK_WARN_GB:
        out.append(("sys_disk",
                    f"🚨 system drive low: {sys_free:.1f}GB free — Windows, "
                    "Claude transcripts and temp files start failing here. "
                    "Free space soon."))
    proj_free = snap.get("proj_free_gb")
    if proj_free is not None and proj_free < PROJ_DISK_WARN_GB:
        out.append(("proj_disk",
                    f"⚠️ project drive low: {proj_free:.1f}GB free."))
    q = snap.get("max_queue", 0)
    if q >= QUEUE_WARN:
        out.append(("queue",
                    f"⚠️ a session has {q} messages queued and isn't draining "
                    "— it may be stuck. /interrupt to cut in, or /restart."))
    crashes = snap.get("crashes", 0)
    if crashes >= CRASH_WARN:
        out.append(("crashes",
                    f"⚠️ {crashes} session crashes in the last hour — something "
                    "is unstable. Check /logs."))
    return out
