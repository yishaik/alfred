#!/usr/bin/env python3
"""Generate the box status page and publish it to here.now.

A status page that is written once is wrong by the next morning, so this is a
generator: it reads live state every run and republishes to the same slug. Wire
it to a timer, or run it by hand after changing something.
"""
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SLUG = os.environ.get("STATUS_SLUG", "")            # empty on the first run
OUT = Path("/tmp/box-status")
PUBLISH = Path.home() / ".agents/skills/here-now/scripts/publish.sh"

SERVICES = [
    ("alfred", "גשר ראשי", "@AlfredTheTBot · ~/git"),
    ("alfred-tlvquest", "סוכן TLV-quest", "@TlvQuestAgentBot · ~/git/TLV-quest"),
    ("alfred-storycut", "סוכן storycut", "@StorycutAgentBot · ~/git/storycut"),
    ("understudy-dashboard", "דשבורד understudy", "127.0.0.1:8765 → tailnet"),
    ("tailscaled", "Tailscale", "SSH ו-dashboard עוברים כאן"),
    ("docker", "Docker", ""),
]
TIMERS = [
    ("alfred-backup.timer", "גיבוי state יומי", "03:00"),
    ("second-brain-ingest.timer", "הטמעת second-brain", "05:30, רק עם בקלוג"),
    ("understudy-drain.timer", "ניקוז תור understudy", "כבוי בכוונה — עולה טוקנים"),
]


def sh(cmd, default=""):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=25)
        return (r.stdout or "").strip() or default
    except Exception:
        return default


def unit(name):
    return sh(f"systemctl is-active {name}", "unknown"), sh(f"systemctl is-enabled {name}", "unknown")


def collect():
    d = {}
    d["services"] = [(n, label, note, *unit(n)) for n, label, note in SERVICES]
    d["timers"] = []
    for n, label, when in TIMERS:
        state = sh(f"systemctl is-enabled {n}", "unknown")
        nxt = sh("systemctl list-timers --all --no-pager | grep -m1 " + n + " | awk '{print $1, $2, $3}'", "—")
        d["timers"].append((n, label, when, state, nxt if state == "enabled" else "—"))

    d["uptime"] = sh("uptime -p", "?")
    d["kernel"] = sh("uname -r", "?")
    d["disk"] = sh("df -h / | tail -1 | awk '{print $4\" פנוי מתוך \"$2}'", "?")
    d["mem"] = sh("free -g | awk '/Mem:/{print $7\"G זמין מתוך \"$2\"G\"}'", "?")
    d["reboot"] = "נדרש" if Path("/var/run/reboot-required").exists() else "לא"
    d["updates"] = sh("apt-get -s upgrade 2>/dev/null | grep -ciE '^Inst.*security'", "?")

    # Backups: the offsite mirror is the thing that actually matters.
    repo = Path("/home/ubuntu/alfred-state-backup")
    if repo.is_dir():
        d["backup_last"] = sh(f"git -C {repo} log -1 --format='%ad' --date=format:'%Y-%m-%d %H:%M'", "?")
        d["backup_count"] = sh(f"ls {repo}/state-*.zip 2>/dev/null | wc -l", "0")
        d["backup_sync"] = sh(f"git -C {repo} status -sb | head -1 | grep -c ahead", "0")
    else:
        d["backup_last"], d["backup_count"], d["backup_sync"] = "ריפו חסר", "0", "0"

    brain = Path("/home/ubuntu/git/second-brain")
    d["wiki"] = sh(f"ls {brain}/wiki/*.md 2>/dev/null | wc -l", "0")
    d["raw"] = sh(f"ls {brain}/raw/*.md 2>/dev/null | wc -l", "0")
    d["backlog"] = sh(f"grep -lE '^ingested:[[:space:]]*false' {brain}/raw/*.md 2>/dev/null | wc -l", "0")
    d["newest_capture"] = sh(f"ls {brain}/raw/ 2>/dev/null | grep -oE '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}' | sort | tail -1", "—")

    q = Path("/home/ubuntu/git/understudy/Projects")
    d["queued"] = len([p for p in q.glob("*/instructions.md")]) if q.is_dir() else 0
    d["projects"] = len([p for p in q.iterdir() if p.is_dir()]) if q.is_dir() else 0

    d["peers"] = sh("ss -tlnp 2>/dev/null | grep -cE '4961[789]'", "0")
    d["auths"] = [
        ("GitHub", "gh auth status >/dev/null 2>&1"),
        ("Vercel", "vercel whoami >/dev/null 2>&1"),
        ("Railway", "railway whoami >/dev/null 2>&1"),
        ("Oracle", "oci iam region-subscription list >/dev/null 2>&1"),
    ]
    # Open ends come from TODOS.md so the page can never disagree with the
    # backlog — one source of truth, not two that drift.
    d["todos"] = []
    todo_file = Path("/home/ubuntu/alfred/TODOS.md")
    if todo_file.is_file():
        section = None
        for line in todo_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("### "):
                section = line[4:].strip()
            elif line.startswith("## "):
                section = None
            elif line.strip().startswith("- [ ]") and section:
                item = re.sub(r"\*\*(.+?)\*\*", r"\1", line.strip()[5:].strip())
                item = re.sub(r"`(.+?)`", r"\1", item)
                d["todos"].append((section, item.split(" — ")[0].strip(),
                                   (item.split(" — ", 1)[1] if " — " in item else "")))

    # x-reader: absorbed from the separate dashboard page, which was a snapshot
    # and had been showing July numbers all week.
    xr = Path("/home/ubuntu/git/x-reader")
    d["xr_last"] = sh("systemctl show -p InactiveEnterTimestamp --value "
                      "x-reader-daily.service | cut -d' ' -f2-3", "—")
    d["xr_ok"] = sh("systemctl show -p ExecMainStatus --value x-reader-daily.service", "?") == "0"
    for name, key in (("candidates", "xr_cand"), ("highlighted", "xr_hi"),
                      ("following", "xr_follow")):
        try:
            v = json.loads((xr / f"{name}.json").read_text(encoding="utf-8"))
            d[key] = len(v) if isinstance(v, (list, dict)) else 0
        except Exception:
            d[key] = 0

    # here.now pages, straight from the watcher's snapshot. A status page that
    # cannot tell you which of your published pages went stale is missing the
    # failure that prompted it.
    d["sites"] = []
    try:
        meta = json.loads((Path("/home/ubuntu/alfred/state/herenow-sites.json"))
                          .read_text(encoding="utf-8")).get("sites", {})
        today = datetime.now(timezone.utc).date()
        for slug, s in sorted(meta.items(), key=lambda kv: kv[1].get("name") or kv[0]):
            stamp = s.get("content_at") or s.get("updated") or ""
            try:
                dd = datetime.fromisoformat(stamp.replace("Z", "+00:00")).date()
                days = (today - dd).days
            except Exception:
                dd, days = None, 999
            d["sites"].append((s.get("name") or slug, s.get("url") or "", days,
                               dd.isoformat() if dd else "—",
                               bool(s.get("content_at"))))
    except Exception:
        pass

    d["auth_results"] = []
    for label, cmd in d["auths"]:
        ok = subprocess.run(cmd, shell=True, capture_output=True,
                            env={**os.environ, "PATH": os.environ.get("PATH", "") + ":/home/ubuntu/.local/bin"}).returncode == 0
        d["auth_results"].append((label, ok))
    return d


def dot(state):
    return "s-good" if state in ("active", "enabled") else ("s-warn" if state == "disabled" else "s-crit")


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def render(d):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    rows = "".join(
        f'<div class="row"><span class="dot {dot(act)}"></span>'
        f'<div><b>{esc(label)}</b><div class="sub">{esc(note) or "&nbsp;"}</div></div>'
        f'<div class="state">{esc(act)}<div class="sub">{esc(en)}</div></div></div>'
        for n, label, note, act, en in d["services"])
    trows = "".join(
        f'<div class="row"><span class="dot {dot(state)}"></span>'
        f'<div><b>{esc(label)}</b><div class="sub">{esc(when)}</div></div>'
        f'<div class="state">{esc(state)}<div class="sub">{esc(nxt)}</div></div></div>'
        for n, label, when, state, nxt in d["timers"])
    arows = "".join(
        f'<span class="chip"><span class="dot {"s-good" if ok else "s-crit"}"></span>{esc(l)}</span>'
        for l, ok in d["auth_results"])

    todorows = "".join(
        f'<div class="row"><span class="dot {"s-warn" if "משתמש" in sect else "s-acc"}"></span>'
        f'<div><b>{esc(title)}</b><div class="sub">{esc(detail)[:150] or "&nbsp;"}</div></div>'
        f'<div class="state">{esc(sect)}</div></div>'
        for sect, title, detail in d["todos"]) or '<div class="row"><div>אין קצוות פתוחים</div></div>'

    # Age is the whole point of this block, so it drives the dot: green only
    # while a page is plausibly current, red once it has clearly drifted.
    sitesrows = "".join(
        f'<div class="row">'
        f'<span class="dot {"s-good" if days <= 1 else "s-warn" if days <= 7 else "s-crit"}"></span>'
        f'<div><b>{esc(name)}</b><div class="sub">'
        f'<a href="{esc(url)}" target="_blank" rel="noopener">{esc(url)}</a></div></div>'
        f'<div class="state">{"היום" if days <= 0 else "אתמול" if days == 1 else f"לפני {days} ימים" if days < 900 else "—"}'
        f'<div class="sub">{esc(stamp)}{"" if verified else " · שינוי אחרון"}</div></div></div>'
        for name, url, days, stamp, verified in d["sites"]) or \
        '<div class="row"><div>אין נתונים</div></div>'

    backup_warn = "" if d["backup_sync"] == "0" else '<div class="warn">⚠️ יש commit גיבוי שלא נדחף</div>'
    stale = f'<div class="warn">⚠️ אין קליטה חדשה מאז {esc(d["newest_capture"])} — x-reader לא רץ כאן</div>'

    return f"""<!DOCTYPE html>
<html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>מצב התיבה</title><style>
:root{{color-scheme:light dark;--s:#fcfcfb;--p:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;--mut:#898781;
--grid:#e1e0d9;--ring:rgba(11,11,11,.10);--good:#0ca30c;--warn:#fab219;--crit:#d03b3b;--acc:#2a78d6}}
@media(prefers-color-scheme:dark){{:root{{--s:#1a1a19;--p:#0d0d0d;--ink:#fff;--ink2:#c3c2b7;
--grid:#2c2c2a;--ring:rgba(255,255,255,.10);--acc:#3987e5}}}}
*{{box-sizing:border-box}}body{{margin:0;padding:20px 14px 56px;background:var(--p);color:var(--ink);
font-family:system-ui,-apple-system,"Segoe UI",sans-serif;font-size:16px;line-height:1.55}}
.w{{max-width:760px;margin:0 auto}}h1{{font-size:1.5rem;margin:0 0 4px;letter-spacing:-.02em}}
.meta{{color:var(--ink2);font-size:.86rem;margin:0 0 22px}}
h2{{font-size:1.05rem;margin:28px 0 10px;padding-top:16px;border-top:1px solid var(--grid)}}
.card{{background:var(--s);border:1px solid var(--ring);border-radius:12px;overflow:hidden}}
.row{{display:grid;grid-template-columns:auto 1fr auto;gap:11px;align-items:center;
padding:11px 14px;border-bottom:1px solid var(--grid)}}.row:last-child{{border-bottom:none}}
.dot{{width:10px;height:10px;border-radius:50%;flex:none}}
.s-good{{background:var(--good)}}.s-warn{{background:var(--warn)}}.s-crit{{background:var(--crit)}}.s-acc{{background:var(--acc)}}
.sub{{font-size:.76rem;color:var(--mut)}}.state{{text-align:end;font-size:.82rem;color:var(--ink2);
font-variant-numeric:tabular-nums}}
.tiles{{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}}
@media(min-width:560px){{.tiles{{grid-template-columns:repeat(4,1fr)}}}}
.tile{{background:var(--s);border:1px solid var(--ring);border-radius:12px;padding:12px 14px}}
.tile .v{{font-size:1.45rem;font-weight:650;line-height:1.1}}.tile .k{{font-size:.76rem;color:var(--ink2)}}
.chip{{display:inline-flex;align-items:center;gap:6px;background:var(--s);border:1px solid var(--ring);
border-radius:20px;padding:5px 12px;margin:0 0 6px 6px;font-size:.84rem}}
.warn{{background:var(--s);border:1px solid var(--ring);border-inline-start:3px solid var(--warn);
border-radius:10px;padding:10px 13px;margin-top:10px;font-size:.86rem}}
footer{{margin-top:34px;padding-top:14px;border-top:1px solid var(--grid);color:var(--mut);font-size:.78rem}}
</style></head><body><div class="w">

<h1>מצב התיבה</h1>
<p class="meta">Oracle ARM64 · {esc(d['uptime'])} · kernel {esc(d['kernel'])} · עודכן {now}</p>

<div class="tiles">
  <div class="tile"><div class="v">{esc(d['disk'].split()[0])}</div><div class="k">דיסק פנוי</div></div>
  <div class="tile"><div class="v">{esc(d['mem'].split()[0])}</div><div class="k">זיכרון זמין</div></div>
  <div class="tile"><div class="v">{esc(d['updates'])}</div><div class="k">עדכוני אבטחה</div></div>
  <div class="tile"><div class="v">{esc(d['reboot'])}</div><div class="k">אתחול נדרש</div></div>
</div>

<h2>שירותים</h2><div class="card">{rows}</div>

<h2>משימות מתוזמנות</h2><div class="card">{trows}</div>

<h2>גיבויים</h2><div class="card">
<div class="row"><span class="dot {'s-good' if d['backup_sync']=='0' else 's-warn'}"></span>
<div><b>alfred-state-backup</b><div class="sub">מראה offsite ב-GitHub</div></div>
<div class="state">{esc(d['backup_count'])} קבצים<div class="sub">{esc(d['backup_last'])}</div></div></div>
</div>{backup_warn}

<h2>ידע ותור</h2>
<div class="tiles">
  <div class="tile"><div class="v">{esc(d['wiki'])}</div><div class="k">דפי wiki</div></div>
  <div class="tile"><div class="v">{esc(d['raw'])}</div><div class="k">קליטות</div></div>
  <div class="tile"><div class="v">{esc(d['backlog'])}</div><div class="k">ממתינות להטמעה</div></div>
  <div class="tile"><div class="v">{d['queued']}</div><div class="k">בתור understudy</div></div>
</div>{stale}

<h2>x-reader</h2>
<div class="tiles">
  <div class="tile"><div class="v">{d['xr_cand']}</div><div class="k">מועמדים</div></div>
  <div class="tile"><div class="v">{d['xr_hi']}</div><div class="k">מסומנים</div></div>
  <div class="tile"><div class="v">{d['xr_follow']}</div><div class="k">נעקבים</div></div>
</div>
<div class="card"><div class="row">
<span class="dot {'s-good' if d['xr_ok'] else 's-crit'}"></span>
<div><b>ריצה יומית אחרונה</b><div class="sub">scouts · search · enrich · ingest</div></div>
<div class="state">{esc(d['xr_last'])}</div></div></div>

<h2>הדפים שפורסמו</h2><div class="card">{sitesrows}</div>

<h2>קצוות פתוחים</h2><div class="card">{todorows}</div>

<h2>אימותים</h2><div>{arows}</div>

<h2>רשת</h2><div class="card">
<div class="row"><span class="dot {'s-good' if d['peers']!='0' else 's-warn'}"></span>
<div><b>peer bus</b><div class="sub">תקשורת בין הסוכנים, 127.0.0.1:49617-49619</div></div>
<div class="state">{esc(d['peers'])}/3 מאזינים</div></div>
<div class="row"><span class="dot s-good"></span>
<div><b>SSH</b><div class="sub">דרך Tailscale בלבד; פורט 22 חסום מבחוץ</div></div>
<div class="state">מוגן</div></div>
</div>

<footer>נוצר אוטומטית מ-status_page.py · הרץ שוב כדי לרענן</footer>
</div></body></html>"""


def main():
    d = collect()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "index.html").write_text(render(d), encoding="utf-8")
    if not PUBLISH.is_file():
        print("publish.sh not found — page written to", OUT)
        return 0
    cmd = [str(PUBLISH), str(OUT), "--client", "claude-code"]
    if SLUG:
        cmd += ["--slug", SLUG]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    out = (r.stdout or "") + (r.stderr or "")
    m = re.search(r"publish_result\.slug=(\S+)", out)
    print(out.strip()[-600:])
    if m and not SLUG:
        print("\n>>> set STATUS_SLUG=" + m.group(1))
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
