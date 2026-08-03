#!/usr/bin/env python3
"""Alfred dashboard — machine state, Oracle fleet, and a live task list.

Two-way by design:
  · machine → board: every field is read fresh on each load, nothing is cached
    into the page. Change something on the box and a reload shows it.
  · board → machine: tasks carry an optional `check` predicate. When the
    predicate passes the task closes itself with a timestamp — you never tick
    "rotate the token" by hand, rotating it is what closes it. Tasks with no
    predicate get a manual toggle that writes straight back to tasks.json.

Served on the tailnet only:
    tailscale serve --bg --https 443 --set-path /alfred http://127.0.0.1:8771

Stdlib only. Same house style as secretbox.py and the understudy dashboard.
"""
import hmac
import json
import os
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

ALFRED = Path("/home/ubuntu/alfred")
TASKS = ALFRED / "tasks.json"
HOST = os.environ.get("DASH_HOST", "127.0.0.1")
PORT = int(os.environ.get("DASH_PORT", "8771"))
TENANCY = ("ocid1.tenancy.oc1..aaaaaaaahd6mzl2lzrbqcaty54ooauccajk3k6grg7"
           "mucbstjmh6lydst4va")

SERVICES = [
    ("alfred", "גשר ראשי", "@AlfredTheTBot · ~/git"),
    ("alfred-tlvquest", "סוכן TLV-quest", "@TlvQuestAgentBot"),
    ("alfred-storycut", "סוכן storycut", "@StorycutAgentBot"),
    ("secretbox", "Secretbox", "העברת סודות · tailnet"),
    ("understudy-dashboard", "דשבורד understudy", "127.0.0.1:8765"),
    ("tailscaled", "Tailscale", "SSH ו-dashboards"),
]
TIMERS = [
    ("alfred-backup.timer", "גיבוי state", "יומי 03:00"),
    ("second-brain-ingest.timer", "הטמעת second-brain", "יומי 05:30, רק עם בקלוג"),
    ("box-status.timer", "רענון דף המצב", "כל 6 שעות"),
    ("secret-audit.timer", "ביקורת סודות", "שבועי, ראשון 09:00"),
    ("understudy-drain.timer", "ניקוז תור understudy", "כבוי בכוונה"),
]

_oci_cache = {"at": 0.0, "data": []}


def sh(cmd, default="", timeout=25):
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           timeout=timeout,
                           env={**os.environ,
                                "PATH": os.environ.get("PATH", "") + ":/home/ubuntu/.local/bin"})
        return (r.stdout or "").strip() or default
    except Exception:
        return default


def load_tasks():
    try:
        return json.loads(TASKS.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_tasks(tasks):
    fd, tmp = tempfile.mkstemp(dir=str(TASKS.parent))
    try:
        os.write(fd, json.dumps(tasks, ensure_ascii=False, indent=1).encode())
        os.close(fd)
        os.chmod(tmp, 0o600)
        os.replace(tmp, TASKS)
    except Exception:
        os.unlink(tmp)
        raise


def run_checks(tasks):
    """Close tasks whose predicate now passes; reopen ones that regressed.

    Reopening matters: if the peer bus drops back to 2/3 the task is live again
    rather than sitting closed on a stale timestamp.
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    changed = False
    for t in tasks:
        chk = t.get("check")
        if not chk:
            continue
        ok = subprocess.run(chk, shell=True, capture_output=True).returncode == 0
        if ok and not t.get("done_at"):
            t["done_at"], t["updated"], t["closed_by"] = now, now, "auto"
            changed = True
        elif not ok and t.get("done_at") and t.get("closed_by") == "auto":
            t["done_at"], t["updated"] = None, now
            changed = True
    if changed:
        save_tasks(tasks)
    return tasks


def oracle_fleet():
    """OCI is slow (~2s); cache for 5 minutes so a reload stays snappy."""
    if time.time() - _oci_cache["at"] < 300 and _oci_cache["data"]:
        return _oci_cache["data"]
    raw = sh("oci compute instance list --compartment-id " + TENANCY +
             " --query 'data[].{name:\"display-name\",state:\"lifecycle-state\","
             "shape:shape,created:\"time-created\",ocpus:\"shape-config\".ocpus,"
             "mem:\"shape-config\".\"memory-in-gbs\"}' --output json", "[]", timeout=40)
    try:
        data = json.loads(raw)
    except Exception:
        data = []
    _oci_cache.update(at=time.time(), data=data)
    return data


def rel_days(iso):
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        n = (datetime.now(timezone.utc) - d).days
        return f"{n} ימים" if n else "היום"
    except Exception:
        return "—"


def fmt_ts(iso):
    try:
        return datetime.fromisoformat(iso).strftime("%d/%m %H:%M")
    except Exception:
        return "—"


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def collect():
    d = {"uptime": sh("uptime -p"), "kernel": sh("uname -r"),
         "disk": sh("df -h / | tail -1 | awk '{print $4}'"),
         "mem": sh("free -g | awk '/Mem:/{print $7\"G\"}'"),
         "load": sh("cut -d' ' -f1-3 /proc/loadavg"),
         "reboot": "נדרש" if Path("/var/run/reboot-required").exists() else "לא",
         "updates": sh("apt-get -s upgrade 2>/dev/null | grep -ciE '^Inst.*security'", "0"),
         "boot": sh("uptime -s")}
    d["services"] = [(l, n, sh(f"systemctl is-active {n}", "?"),
                      sh(f"systemctl is-enabled {n}", "?")) for n, l, n2 in
                     [(n, l, x) for n, l, x in SERVICES]]
    d["services"] = [(l, note, sh(f"systemctl is-active {n}", "?"),
                      sh(f"systemctl is-enabled {n}", "?")) for n, l, note in SERVICES]
    d["timers"] = [(l, when, sh(f"systemctl is-enabled {n}", "?"),
                    sh("systemctl list-timers --all --no-pager | grep -m1 " + n +
                       " | awk '{print $1, $2, $3}'", "—")) for n, l, when in TIMERS]
    d["fleet"] = oracle_fleet()
    d["peers"] = sh("ss -tln | grep -cE ':4961[789]'", "0")
    repo = Path("/home/ubuntu/alfred-state-backup")
    d["backup"] = sh(f"git -C {repo} log -1 --format='%ad' --date=format:'%d/%m %H:%M'", "—")
    d["backup_ahead"] = sh(f"git -C {repo} status -sb | head -1 | grep -c ahead", "0")
    brain = Path("/home/ubuntu/git/second-brain")
    d["wiki"] = sh(f"ls {brain}/wiki/*.md 2>/dev/null | wc -l", "0")
    d["backlog"] = sh(f"grep -lE '^ingested:[[:space:]]*false' {brain}/raw/*.md 2>/dev/null | wc -l", "0")
    d["secrets"] = sh("python3 /home/ubuntu/alfred/secretbox.py --audit 2>/dev/null | grep -c '●'", "0")
    return d


def render(d, tasks, msg=""):
    now = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    open_t = [t for t in tasks if not t.get("done_at")]
    done_t = [t for t in tasks if t.get("done_at")]

    def task_card(t):
        auto = "אוטומטי" if t.get("check") else "ידני"
        who = "אתה" if t.get("owner") == "you" else "אלפרד"
        done = bool(t.get("done_at"))
        stamp = (f'נסגר {fmt_ts(t["done_at"])} · {t.get("closed_by","ידני")}'
                 if done else f'נפתח {fmt_ts(t["created"])}')
        if t.get("kind") == "decision":
            opts = "".join(f'<span class="opt">{esc(o)}</span>' for o in t.get("options", []))
            body = f'<div class="opts">{opts}</div>'
        elif t.get("check"):
            body = f'<div class="chk">נסגר מעצמו כש: <code>{esc(t["check"])[:80]}</code></div>'
        else:
            body = ""
        # Auto-checked tasks get no manual toggle — the machine owns their state —
        # but they are still deletable, and anything hand-added can be removed.
        toggle = ("" if t.get("check") else
                  f'<button name="act" value="{"reopen" if done else "done"}">'
                  f'{"החזר לפתוח" if done else "סמן כבוצע"}</button>')
        btn = (f'<form method="POST" style="margin:0"><input type="hidden" name="id" value="{t["id"]}">'
               f'{toggle}<button name="act" value="del" class="del" '
               f'onclick="return confirm(\'למחוק את המשימה?\')">מחק</button></form>')
        return (f'<div class="task{" dn" if done else ""}">'
                f'<div class="thd"><b>{esc(t["title"])}</b>'
                f'<span class="tag">{who} · {auto}</span></div>'
                f'<div class="det">{esc(t.get("detail",""))}</div>{body}'
                f'<div class="stamp">{stamp}</div>{btn}</div>')

    fleet = "".join(
        f'<div class="row"><span class="dot {"ok" if i.get("state")=="RUNNING" else "no"}"></span>'
        f'<div><b>{esc(i.get("name"))}</b><div class="sub">{esc(i.get("shape"))} · '
        f'{i.get("ocpus")} OCPU · {i.get("mem")}GB</div></div>'
        f'<div class="st">{esc(i.get("state"))}<div class="sub">קיים {rel_days(i.get("created",""))}</div></div></div>'
        for i in d["fleet"]) or '<div class="row"><div>לא ניתן לקרוא מ-OCI</div></div>'

    svc = "".join(
        f'<div class="row"><span class="dot {"ok" if a=="active" else "no"}"></span>'
        f'<div><b>{esc(l)}</b><div class="sub">{esc(note)}</div></div>'
        f'<div class="st">{esc(a)}<div class="sub">{esc(e)}</div></div></div>'
        for l, note, a, e in d["services"])
    tim = "".join(
        f'<div class="row"><span class="dot {"ok" if e=="enabled" else "warn"}"></span>'
        f'<div><b>{esc(l)}</b><div class="sub">{esc(w)}</div></div>'
        f'<div class="st">{esc(e)}<div class="sub">{esc(nx) if e=="enabled" else "—"}</div></div></div>'
        for l, w, e, nx in d["timers"])

    return f"""<!DOCTYPE html><html lang="he" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="120"><title>Alfred</title><style>
:root{{color-scheme:light dark;--s:#fcfcfb;--p:#f9f9f7;--ink:#0b0b0b;--ink2:#52514e;--mut:#898781;
--grid:#e1e0d9;--ring:rgba(11,11,11,.10);--good:#0ca30c;--warn:#fab219;--acc:#2a78d6}}
@media(prefers-color-scheme:dark){{:root{{--s:#1a1a19;--p:#0d0d0d;--ink:#fff;--ink2:#c3c2b7;
--grid:#2c2c2a;--ring:rgba(255,255,255,.10);--acc:#3987e5}}}}
*{{box-sizing:border-box}}body{{margin:0;padding:18px 13px 60px;background:var(--p);color:var(--ink);
font-family:system-ui,-apple-system,sans-serif;line-height:1.5}}.w{{max-width:760px;margin:0 auto}}
h1{{font-size:1.45rem;margin:0 0 3px}}.meta{{color:var(--ink2);font-size:.82rem;margin:0 0 18px}}
h2{{font-size:1.05rem;margin:26px 0 9px;padding-top:15px;border-top:1px solid var(--grid)}}
.tiles{{display:grid;grid-template-columns:repeat(3,1fr);gap:9px}}
@media(min-width:560px){{.tiles{{grid-template-columns:repeat(6,1fr)}}}}
.tile{{background:var(--s);border:1px solid var(--ring);border-radius:11px;padding:10px;text-align:center}}
.tile .v{{font-size:1.15rem;font-weight:650;line-height:1.15}}.tile .k{{font-size:.68rem;color:var(--ink2)}}
.card{{background:var(--s);border:1px solid var(--ring);border-radius:12px;overflow:hidden}}
.row{{display:grid;grid-template-columns:auto 1fr auto;gap:10px;align-items:center;padding:10px 13px;
border-bottom:1px solid var(--grid)}}.row:last-child{{border-bottom:none}}
.dot{{width:9px;height:9px;border-radius:50%}}.ok{{background:var(--good)}}.no{{background:#d03b3b}}
.warn{{background:var(--warn)}}
.sub{{font-size:.73rem;color:var(--mut)}}.st{{text-align:end;font-size:.79rem;color:var(--ink2)}}
.task{{background:var(--s);border:1px solid var(--ring);border-radius:11px;padding:12px 14px;margin-bottom:9px}}
.task.dn{{opacity:.55}}.task.dn b{{text-decoration:line-through}}
.thd{{display:flex;justify-content:space-between;gap:8px;align-items:baseline}}
.tag{{font-size:.67rem;color:var(--mut);white-space:nowrap}}
.det{{font-size:.84rem;color:var(--ink2);margin:3px 0 6px}}
.chk{{font-size:.72rem;color:var(--mut);margin-bottom:5px}}
code{{background:color-mix(in srgb,var(--ink) 8%,transparent);padding:1px 4px;border-radius:3px}}
.opts{{margin:4px 0 6px}}.opt{{display:inline-block;font-size:.75rem;border:1px solid var(--ring);
border-radius:20px;padding:2px 9px;margin-inline-end:5px;color:var(--ink2)}}
.stamp{{font-size:.71rem;color:var(--mut)}}
button{{margin-top:7px;margin-inline-end:6px;padding:6px 13px;font-size:.8rem;
border:1px solid var(--ring);border-radius:8px;background:var(--p);color:var(--ink)}}
button.del{{color:var(--mut)}}
.add{{background:var(--s);border:1px solid var(--ring);border-radius:11px;padding:12px 14px;margin-bottom:11px}}
.add input,.add select{{width:100%;padding:9px 11px;font-size:16px;margin-bottom:7px;
border:1px solid var(--ring);border-radius:8px;background:var(--p);color:var(--ink)}}
.arow{{display:flex;gap:8px;align-items:center}}.arow select{{flex:1;margin:0}}
.arow button{{margin:0;flex:0 0 auto;background:var(--acc);color:#fff;border-color:transparent;font-weight:600}}
.msg{{background:var(--s);border:1px solid var(--ring);border-inline-start:3px solid var(--acc);
border-radius:10px;padding:10px 13px;margin-bottom:14px;font-size:.87rem}}
a{{color:var(--acc)}}
</style></head><body><div class="w">
<h1>Alfred</h1>
<p class="meta">{esc(d['uptime'])} · עלה {esc(d['boot'])} · kernel {esc(d['kernel'])} · נטען {now} · מתרענן לבד כל 2 דק'</p>
{f'<div class="msg">{msg}</div>' if msg else ''}

<div class="tiles">
<div class="tile"><div class="v">{esc(d['disk'])}</div><div class="k">דיסק</div></div>
<div class="tile"><div class="v">{esc(d['mem'])}</div><div class="k">זיכרון</div></div>
<div class="tile"><div class="v">{esc(d['load'].split()[0] if d['load'] else '—')}</div><div class="k">עומס</div></div>
<div class="tile"><div class="v">{esc(d['updates'])}</div><div class="k">עדכוני אבטחה</div></div>
<div class="tile"><div class="v">{esc(d['peers'])}/3</div><div class="k">peer bus</div></div>
<div class="tile"><div class="v">{esc(d['backlog'])}</div><div class="k">בקלוג ידע</div></div>
</div>

<h2>משימות פתוחות · {len(open_t)}</h2>
<form method="POST" class="add">
<input type="hidden" name="act" value="add">
<input name="title" placeholder="משימה חדשה" required maxlength="120">
<input name="detail" placeholder="פרטים (לא חובה)" maxlength="300">
<div class="arow">
<select name="owner"><option value="you">אני</option><option value="me">אלפרד</option></select>
<button type="submit">הוסף</button></div>
</form>
{"".join(task_card(t) for t in open_t) or '<div class="task">אין משימות פתוחות</div>'}

<h2>מכונות Oracle</h2><div class="card">{fleet}</div>

<h2>שירותים</h2><div class="card">{svc}</div>

<h2>משימות מתוזמנות</h2><div class="card">{tim}</div>

<h2>גיבוי וידע</h2><div class="card">
<div class="row"><span class="dot {'ok' if d['backup_ahead']=='0' else 'warn'}"></span>
<div><b>גיבוי offsite</b><div class="sub">alfred-state-backup ב-GitHub</div></div>
<div class="st">{esc(d['backup'])}</div></div>
<div class="row"><span class="dot ok"></span><div><b>second-brain</b>
<div class="sub">{esc(d['wiki'])} דפים · {esc(d['backlog'])} ממתינות</div></div>
<div class="st"><a href="/secrets">Secretbox</a></div></div>
</div>

<h2>הושלם · {len(done_t)}</h2>
{"".join(task_card(t) for t in done_t) or '<div class="task">עדיין כלום</div>'}

</div></body></html>"""


class H(BaseHTTPRequestHandler):
    server_version = "alfred-dash"

    def _identity_ok(self) -> bool:
        """Same tailnet identity gate as secretbox and the understudy dashboard.

        `tailscale serve` sets Tailscale-User-Login and strips any client copy,
        so it cannot be forged from outside; its absence means the request never
        went through the proxy and came straight to loopback, which already
        requires being on this box."""
        expect = os.environ.get("DASH_LOGIN", "").strip()
        if not expect:
            return True
        got = self.headers.get("Tailscale-User-Login", "")
        if not got:
            return self.client_address[0] in ("127.0.0.1", "::1")
        return hmac.compare_digest(got, expect)

    def parse_request(self):
        # Gated here rather than per-verb: this handler grew a do_POST after the
        # do_GET, and the next verb added would silently miss a per-method check.
        if not super().parse_request():
            return False
        if not self._identity_ok():
            self.send_error(403, "not your tailnet identity")
            return False
        return True

    def log_message(self, *a):
        pass

    def _out(self, msg=""):
        tasks = run_checks(load_tasks())
        body = render(collect(), tasks, msg).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._out()

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        form = parse_qs(self.rfile.read(min(n, 8192)).decode())
        tid = (form.get("id") or [""])[0]
        act = (form.get("act") or [""])[0]
        tasks = load_tasks()
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        msg = ""

        if act == "add":
            title = (form.get("title") or [""])[0].strip()[:120]
            if not title:
                self._out("לא הוזן שם למשימה.")
                return
            base = "".join(c if c.isalnum() else "-" for c in title.lower())[:24].strip("-")
            tid = base or "task"
            existing = {t["id"] for t in tasks}
            while tid in existing:
                tid += "-2"
            # No `check` field: a predicate is a shell command that then runs on
            # every load, so it is never taken from a web form.
            tasks.append({"id": tid, "title": title,
                          "detail": (form.get("detail") or [""])[0].strip()[:300],
                          "owner": (form.get("owner") or ["you"])[0],
                          "kind": "task", "check": None,
                          "created": now, "updated": now, "done_at": None})
            msg = f"➕ נוספה: {title}"
            save_tasks(tasks)
            self._out(msg)
            return

        if act == "del":
            gone = [t for t in tasks if t["id"] == tid]
            tasks = [t for t in tasks if t["id"] != tid]
            msg = f"🗑 נמחקה: {gone[0]['title']}" if gone else "לא נמצאה."
            save_tasks(tasks)
            self._out(msg)
            return

        for t in tasks:
            if t["id"] == tid:
                if act == "done":
                    t["done_at"], t["closed_by"] = now, "ידני"
                    msg = f"✅ {t['title']} — סומן כבוצע"
                else:
                    t["done_at"], t["closed_by"] = None, None
                    msg = f"↩︎ {t['title']} — הוחזר לפתוח"
                t["updated"] = now
        save_tasks(tasks)
        self._out(msg)


if __name__ == "__main__":
    print(f"alfred dashboard on http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()
