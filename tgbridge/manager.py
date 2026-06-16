"""AgentManager: the registry of agents, live sessions, and message routing.

Routing model
  * private chat            -> the "active" agent (switch via /agents)
  * forum topic (threaded)  -> its own session; bind an agent with /bind
  * bot-to-bot              -> ⟦TO:name⟧ markers and the peer HTTP bus, guarded
                               by hop counters and per-pair rate limits
"""

import asyncio
import logging
import shutil
import time
from datetime import date, datetime, timedelta

from . import metrics
from .config import (AGENTS_FILE, BACKUP_DIR, CHAT_ID, COSTS_FILE, DIGEST_TIME,
                     DREAM_TIME, ESCALATE_MINUTES, HEALTH_TIME,
                     LEGACY_SESSION_FILE, MAX_HOPS, MEMORY_FILE,
                     MONTHLY_BUDGET_USD, PAIR_MSGS_PER_5MIN,
                     PROACTIVE_IDLE_HOURS, PROACTIVE_QUIET_END,
                     PROACTIVE_QUIET_START, ROOT, SESSIONS_FILE, STATE_DIR,
                     TOPICS_FILE, load_json, save_json, system_drive_free_gb)
from .digest import build_digest
from .dream import dream_brief
from .escalate import CRASH_WINDOW_S, assess
from .memory import Memory
from .ratelimit import PairLimiter
from .session import AgentConfig, AgentSession, TurnSource

log = logging.getLogger("bridge.manager")


def job_skey(agent: str, chat_id: int | None, thread_id: int | None) -> str:
    """Session key a scheduled job should be delivered to — the topic it was
    created in, or the agent's private-chat session."""
    if chat_id and chat_id != CHAT_ID:
        return f"{agent}@t{thread_id or 0}"
    return f"{agent}@p"


class AgentManager:
    def __init__(self, app):
        self.app = app
        self.bot = app.bot
        raw = load_json(AGENTS_FILE, {})
        self.agents: dict[str, AgentConfig] = {
            name: AgentConfig.from_dict(name, d)
            for name, d in raw.get("agents", {}).items()}
        if "main" not in self.agents:
            self.agents["main"] = AgentConfig(name="main")
        self.active: str = raw.get("active", "main")
        if self.active not in self.agents:
            self.active = "main"
        self.session_ids: dict[str, str] = load_json(SESSIONS_FILE, {})
        self._migrate_legacy()
        self.topics: dict[str, str] = load_json(TOPICS_FILE, {})  # thread_id -> agent
        self.costs: dict[str, float] = load_json(COSTS_FILE, {})
        raw_mem = load_json(MEMORY_FILE, {})
        self.memories: dict[str, Memory] = {
            name: Memory.from_list(items) for name, items in raw_mem.items()}
        self.sessions: dict[str, AgentSession] = {}
        self.by_sid: dict[int, AgentSession] = {}
        self._sid_seq = 0
        self.pair_limiter = PairLimiter(PAIR_MSGS_PER_5MIN, 300.0)
        self.scheduler = None   # set by main
        self.peers = None       # set by main
        self.started_at = time.time()
        self._budget_warned: set[int] = set()   # thresholds already announced this month
        self._budget_month = date.today().strftime("%Y-%m")
        self._health_task: asyncio.Task | None = None
        self._proactive_task: asyncio.Task | None = None
        self._digest_task: asyncio.Task | None = None
        self._escalate_task: asyncio.Task | None = None
        self._dream_task: asyncio.Task | None = None
        self._crash_times: list[float] = []     # monotonic stamps of recent crashes
        self._active_alerts: set[str] = set()    # escalation keys currently tripped

    def _migrate_legacy(self):
        if "main@p" not in self.session_ids and LEGACY_SESSION_FILE.exists():
            sid = LEGACY_SESSION_FILE.read_text().strip()
            if sid:
                self.session_ids["main@p"] = sid
                save_json(SESSIONS_FILE, self.session_ids)
                log.info("migrated legacy session id for main@p")

    # -- persistence ------------------------------------------------------- #
    def save_agents(self):
        save_json(AGENTS_FILE, {
            "active": self.active,
            "agents": {n: c.to_dict() for n, c in self.agents.items()}})

    def save_session_id(self, skey: str, session_id: str | None):
        if session_id is None:
            self.session_ids.pop(skey, None)
        else:
            self.session_ids[skey] = session_id
        save_json(SESSIONS_FILE, self.session_ids)

    def save_topics(self):
        save_json(TOPICS_FILE, self.topics)

    def memory_for(self, agent: str) -> Memory:
        """The (lazily-created) long-term memory for an agent."""
        mem = self.memories.get(agent)
        if mem is None:
            mem = self.memories[agent] = Memory()
        return mem

    def save_memory(self):
        save_json(MEMORY_FILE, {name: mem.to_list()
                                for name, mem in self.memories.items()
                                if mem.items})

    def decay_memories(self) -> int:
        """Daily maintenance: fade unengaged memory across all agents. Persists
        only if something actually changed. Returns the number of items aged."""
        changed = sum(mem.decay() for mem in self.memories.values())
        if changed:
            self.save_memory()
        return changed

    def add_cost(self, usd: float) -> tuple[float, str | None]:
        """Accumulate cost; returns (today_total, budget_alert|None)."""
        key = date.today().isoformat()
        self.costs[key] = self.costs.get(key, 0.0) + usd
        if len(self.costs) > 90:
            for k in sorted(self.costs)[:-90]:
                del self.costs[k]
        save_json(COSTS_FILE, self.costs)
        alert = None
        if MONTHLY_BUDGET_USD > 0:
            month = date.today().strftime("%Y-%m")
            if month != self._budget_month:
                self._budget_month = month
                self._budget_warned.clear()
            spent = self.month_cost()
            for pct in (100, 80, 50):
                if spent >= MONTHLY_BUDGET_USD * pct / 100 and \
                        pct not in self._budget_warned:
                    self._budget_warned.add(pct)
                    alert = (f"💸 monthly budget: ${spent:.2f} / "
                             f"${MONTHLY_BUDGET_USD:.0f} ({pct}% threshold crossed)")
                    break
        return self.costs[key], alert

    def today_cost(self) -> float:
        return self.costs.get(date.today().isoformat(), 0.0)

    def month_cost(self) -> float:
        month = date.today().strftime("%Y-%m")
        return sum(v for k, v in self.costs.items() if k.startswith(month))

    # -- sessions ------------------------------------------------------------ #
    async def _get_session(self, agent_name: str, skey: str,
                           chat_id: int, thread_id: int | None) -> AgentSession:
        s = self.sessions.get(skey)
        if s:
            return s
        cfg = self.agents.get(agent_name) or self.agents["main"]
        self._sid_seq += 1
        s = AgentSession(self, cfg, skey, self._sid_seq, chat_id, thread_id)
        self.sessions[skey] = s
        self.by_sid[s.sid] = s
        try:
            await s.start(resume=True)
        except Exception as e:
            s.outbox.start()
            s.outbox.emit(f"❌ couldn't start Claude for {agent_name}: {e}")
            log.exception("start failed for %s", skey)
        return s

    async def session_for_route(self, chat_id: int,
                                thread_id: int | None) -> AgentSession:
        if chat_id != CHAT_ID:
            # forum supergroup: every topic (incl. General, thread None) is its
            # own conversation; /bind picks which agent config it runs
            tid = thread_id or 0
            agent = self.topics.get(str(tid), self.active)
            return await self._get_session(agent, f"{agent}@t{tid}",
                                           chat_id, thread_id)
        agent = self.active
        return await self._get_session(agent, f"{agent}@p", chat_id, None)

    async def session_for_agent(self, name: str) -> AgentSession:
        """Private-chat session for a named agent (used for bot-to-bot and
        scheduler deliveries when no topic session exists)."""
        return await self._get_session(name, f"{name}@p", CHAT_ID, None)

    async def session_for_job(self, agent: str, chat_id: int | None,
                              thread_id: int | None) -> AgentSession:
        """Session for a scheduled job, honoring where it was created."""
        if agent not in self.agents:
            agent = self.active if self.active in self.agents else "main"
        skey = job_skey(agent, chat_id, thread_id)
        if skey.endswith("@p"):
            return await self._get_session(agent, skey, CHAT_ID, None)
        return await self._get_session(agent, skey, chat_id, thread_id)

    def find_by_sid(self, sid: int) -> AgentSession | None:
        return self.by_sid.get(sid)

    async def switch_active(self, name: str) -> AgentSession:
        self.active = name
        self.save_agents()
        return await self.session_for_agent(name)

    async def _remove_session(self, skey: str, forget_id: bool = True):
        """Single teardown path so sessions/by_sid/saved-id never desync."""
        s = self.sessions.pop(skey, None)
        if not s:
            return
        self.by_sid.pop(s.sid, None)
        await s.stop()
        await s.outbox.stop()
        if forget_id:
            self.save_session_id(skey, None)

    async def remove_agent(self, name: str):
        for skey in [k for k in self.sessions if k.split("@")[0] == name]:
            await self._remove_session(skey)
        self.agents.pop(name, None)
        if self.active == name:
            self.active = "main"
        self.save_agents()

    async def stop_all(self):
        if self._health_task:
            self._health_task.cancel()
        if self._proactive_task:
            self._proactive_task.cancel()
        if self._digest_task:
            self._digest_task.cancel()
        if self._escalate_task:
            self._escalate_task.cancel()
        if self._dream_task:
            self._dream_task.cancel()
        for s in list(self.sessions.values()):
            await s.stop()
            await s.outbox.stop()

    # -- proactive idle check-ins --------------------------------------------#
    def start_proactive_loop(self):
        """Background loop letting opt-in agents volunteer a thought after a
        long idle gap (issue #5). Disabled wholesale if the idle threshold is
        non-positive."""
        if PROACTIVE_IDLE_HOURS > 0:
            self._proactive_task = asyncio.create_task(self._proactive_loop())

    async def _proactive_loop(self):
        import time as _time
        from .proactive import CHECKIN_PROMPT, should_check_in
        threshold = PROACTIVE_IDLE_HOURS * 3600.0
        while True:
            try:
                await asyncio.sleep(300)   # check every 5 min; idle gap is hours
                now_hour = datetime.now().hour
                for s in list(self.sessions.values()):
                    idle = _time.monotonic() - s.last_activity
                    if not should_check_in(
                            idle, threshold, now_hour,
                            PROACTIVE_QUIET_START, PROACTIVE_QUIET_END,
                            enabled=s.cfg.proactive, busy=s.busy,
                            already_pinged=not s.proactive_armed):
                        continue
                    # fire once per idle stretch; re-armed when the user speaks
                    s.proactive_armed = False
                    await s.feed(CHECKIN_PROMPT, TurnSource(kind="proactive"))
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("proactive loop tick failed")

    # -- daily health report --------------------------------------------------#
    def start_health_loop(self):
        if HEALTH_TIME:
            self._health_task = asyncio.create_task(self._health_loop())

    def health_text(self) -> str:
        up = time.time() - self.started_at
        days, rem = divmod(int(up), 86400)
        busy = sum(1 for s in self.sessions.values() if s.busy)
        lines = [
            "🩺 daily health check",
            f"uptime {days}d{rem // 3600}h · sessions {len(self.sessions)} "
            f"({busy} busy) · jobs {len(self.scheduler.jobs) if self.scheduler else 0}",
            f"cost: today ${self.today_cost():.2f} · month ${self.month_cost():.2f}"
            + (f" / ${MONTHLY_BUDGET_USD:.0f}" if MONTHLY_BUDGET_USD else ""),
        ]
        try:
            free_gb = shutil.disk_usage(str(ROOT)).free / 2**30
            logsz = (ROOT / "bridge.log").stat().st_size / 2**20 \
                if (ROOT / "bridge.log").exists() else 0
            sys_free = system_drive_free_gb()
            sys_note = f" · C: {sys_free:.1f}GB" if sys_free is not None else ""
            lines.append(f"disk free {free_gb:.0f}GB{sys_note} · "
                         f"bridge.log {logsz:.0f}MB")
            if free_gb < 5:
                lines.append("⚠️ low disk space on the project drive!")
            if sys_free is not None and sys_free < 2:
                lines.append("🚨 system drive (C:) nearly FULL — Windows, "
                             "Claude transcripts and temp files will fail. "
                             "Free space ASAP.")
        except Exception:
            pass
        m = metrics.summary()
        if m:
            lines.append(f"counters: {m}")
        return "\n".join(lines)

    def backup_state(self) -> None:
        """Daily zip of state/*.json (agents, sessions, jobs, costs, topics)
        so a corrupted file is recoverable; keeps the newest 7."""
        try:
            import zipfile
            BACKUP_DIR.mkdir(exist_ok=True)
            name = BACKUP_DIR / f"state-{date.today().strftime('%Y%m%d')}.zip"
            if name.exists():
                return
            with zipfile.ZipFile(name, "w", zipfile.ZIP_DEFLATED) as z:
                for p in STATE_DIR.glob("*.json"):
                    z.write(p, p.name)
            for old in sorted(BACKUP_DIR.glob("state-*.zip"))[:-7]:
                old.unlink()
            log.info("state backed up to %s", name.name)
        except Exception:
            log.exception("state backup failed")

    async def _health_loop(self):
        h, m = (int(x) for x in HEALTH_TIME.split(":"))
        while True:
            try:
                now = datetime.now()
                nxt = now.replace(hour=h, minute=m, second=0, microsecond=0)
                if nxt <= now:
                    nxt += timedelta(days=1)
                await asyncio.sleep((nxt - now).total_seconds())
                self.backup_state()
                self.decay_memories()
                await self.bot.send_message(CHAT_ID, self.health_text())
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("health report failed")
                await asyncio.sleep(3600)

    # -- daily digest (issue #7) ---------------------------------------------#
    def start_digest_loop(self):
        if DIGEST_TIME:
            self._digest_task = asyncio.create_task(self._digest_loop())

    async def _digest_loop(self):
        h, m = (int(x) for x in DIGEST_TIME.split(":"))
        while True:
            try:
                now = datetime.now()
                nxt = now.replace(hour=h, minute=m, second=0, microsecond=0)
                if nxt <= now:
                    nxt += timedelta(days=1)
                await asyncio.sleep((nxt - now).total_seconds())
                await self.bot.send_message(CHAT_ID, build_digest(self))
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("digest failed")
                await asyncio.sleep(3600)

    # -- auto-escalation (issue #8) ------------------------------------------#
    def note_crash(self) -> None:
        """A session recorded a crash — feeds the escalation crash signal."""
        now = time.monotonic()
        self._crash_times.append(now)
        self._crash_times = [t for t in self._crash_times
                             if now - t < CRASH_WINDOW_S]

    def recent_crash_count(self) -> int:
        now = time.monotonic()
        self._crash_times = [t for t in self._crash_times
                             if now - t < CRASH_WINDOW_S]
        return len(self._crash_times)

    def _signal_snapshot(self) -> dict:
        try:
            proj_free = shutil.disk_usage(str(ROOT)).free / 2**30
        except OSError:
            proj_free = None
        return {
            "sys_free_gb": system_drive_free_gb(),
            "proj_free_gb": proj_free,
            "max_queue": max((len(s.pending) for s in self.sessions.values()),
                             default=0),
            "crashes": self.recent_crash_count(),
        }

    def start_escalate_loop(self):
        if ESCALATE_MINUTES > 0:
            self._escalate_task = asyncio.create_task(self._escalate_loop())

    async def _escalate_loop(self):
        while True:
            try:
                await asyncio.sleep(ESCALATE_MINUTES * 60)
                alerts = assess(self._signal_snapshot())
                tripped = {k for k, _ in alerts}
                for key, msg in alerts:
                    if key not in self._active_alerts:   # edge-triggered: once
                        await self.bot.send_message(CHAT_ID, msg)
                self._active_alerts = tripped            # cleared keys can re-fire
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("escalation check failed")

    # -- dream mode (issue #9) -----------------------------------------------#
    def start_dream_loop(self):
        if DREAM_TIME:
            self._dream_task = asyncio.create_task(self._dream_loop())

    async def _dream_loop(self):
        import time as _time
        h, m = (int(x) for x in DREAM_TIME.split(":"))
        while True:
            try:
                now = datetime.now()
                nxt = now.replace(hour=h, minute=m, second=0, microsecond=0)
                if nxt <= now:
                    nxt += timedelta(days=1)
                await asyncio.sleep((nxt - now).total_seconds())
                # tidy up, then deliver the morning brief
                self.backup_state()
                self.decay_memories()
                await self.bot.send_message(CHAT_ID, dream_brief(self, _time.time()))
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("dream pass failed")
                await asyncio.sleep(3600)

    # -- markers / bot-to-bot -------------------------------------------------#
    async def handle_markers(self, session: AgentSession, parsed):
        for p in parsed.sends:
            session.outbox.file(p)
        hop = session.turn_source.hop
        for dest, text in parsed.to:
            await self.route_bot_message(session, dest, text, hop + 1)
        if self.scheduler:
            for when, text in parsed.reminds:
                self._add_job(session, "remind", when, text)
            for when, text in parsed.schedules:
                self._add_job(session, "prompt", when, text)
            for jid in parsed.unschedules:
                ok = self.scheduler.cancel(jid)
                session.outbox.emit(f"🗑 job {jid} {'cancelled' if ok else 'not found'}")

    def _add_job(self, session: AgentSession, kind: str, when: str, text: str):
        try:
            job = self.scheduler.add(session, kind, when, text)
            session.outbox.emit(
                f"⏰ scheduled {kind} #{job['id']} for {job['next_human']}"
                + (f" ({job['recur']})" if job.get("recur") else ""))
        except ValueError as e:
            session.outbox.emit(f"⚠️ couldn't schedule: {e}")

    async def route_bot_message(self, src: AgentSession, dest: str, text: str,
                                hop: int):
        """⟦TO:dest⟧ — local agent or HTTP peer. Hop + pair limits prevent loops."""
        dest = dest.strip()
        if hop > MAX_HOPS:
            src.outbox.emit(f"🚦 bot-msg to {dest} dropped: hop limit ({MAX_HOPS}) reached")
            return
        if not self.pair_limiter.allow((src.cfg.name, dest)):
            src.outbox.emit(
                f"🚦 bot-msg to {dest} dropped: pair rate limit "
                f"({PAIR_MSGS_PER_5MIN}/5min). Likely a loop — breaking it.")
            return
        if dest in self.agents:
            if dest == src.cfg.name:
                src.outbox.emit("🚦 bot-msg to self dropped")
                return
            target = await self.session_for_agent(dest)
            target.outbox.emit(f"📨 from {src.cfg.name} (hop {hop}): {text[:300]}")
            await target.feed(f"[bot-msg from {src.cfg.name} hop={hop}] {text}",
                              TurnSource(kind="bot", hop=hop, origin=src.cfg.name))
            return
        if self.peers and self.peers.known(dest):
            ok = await self.peers.send(dest, src.cfg.name, text, hop)
            src.outbox.emit(f"📡 to peer {dest}: {'sent' if ok else 'FAILED'}")
            return
        src.outbox.emit(f"⚠️ unknown bot destination: {dest}")

    async def on_peer_message(self, peer: str, agent: str, text: str, hop: int):
        """Inbound from the HTTP peer bus."""
        if hop > MAX_HOPS:
            log.warning("peer msg from %s dropped: hop %d", peer, hop)
            return
        if not self.pair_limiter.allow((f"peer:{peer}", agent or self.active)):
            log.warning("peer msg from %s dropped: pair rate limit", peer)
            return
        target = await self.session_for_agent(
            agent if agent in self.agents else self.active)
        target.outbox.emit(f"📡 from peer {peer} (hop {hop}): {text[:300]}")
        await target.feed(f"[bot-msg from {peer} hop={hop}] {text}",
                          TurnSource(kind="bot", hop=hop, origin=peer))
