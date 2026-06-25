"""Persistent job scheduler — the engine behind secretary mode.

Jobs come from Claude's ⟦REMIND⟧/⟦SCHEDULE⟧ markers or the user's /remind.
Caps: at most MAX_JOBS jobs, recurrence floor MIN_RECUR_MINUTES, and every
'prompt' firing draws from the target session's non-human turn budget — a
runaway "schedule myself every minute" can't melt the API bill.
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta

import re

from . import metrics
from .config import JOBS_FILE, MAX_JOBS, MIN_RECUR_MINUTES, load_json, save_json
from .markers import next_fire, parse_when
from .session import TurnSource

_UNTIL_RE = re.compile(r"\s+until\s+(\d{4}-\d{2}-\d{2})\s*$", re.IGNORECASE)

log = logging.getLogger("bridge.scheduler")


class Scheduler:
    def __init__(self, mgr):
        self.mgr = mgr
        data = load_json(JOBS_FILE, {"seq": 0, "jobs": []})
        self.seq: int = data.get("seq", 0)
        self.jobs: list[dict] = data.get("jobs", [])
        self._task: asyncio.Task | None = None

    def start(self):
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task      # await so the loop can't GC a pending task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    def _save(self):
        save_json(JOBS_FILE, {"seq": self.seq, "jobs": self.jobs})

    def add(self, session, kind: str, when: str, text: str) -> dict:
        if len(self.jobs) >= MAX_JOBS:
            raise ValueError(f"job limit reached ({MAX_JOBS}); /jobs to prune")
        until_ts = None
        m = _UNTIL_RE.search(when)
        if m:
            until_ts = datetime.strptime(m.group(1), "%Y-%m-%d") \
                .replace(hour=23, minute=59).timestamp()
            when = when[:m.start()]
        nxt, recur = parse_when(when)
        if nxt < datetime.now() - timedelta(seconds=5):
            raise ValueError(f"time is in the past: {when}")
        if until_ts and not recur:
            raise ValueError("'until' only applies to recurring jobs")
        if until_ts and nxt.timestamp() > until_ts:
            raise ValueError("first occurrence is already past the 'until' date")
        if recur and MIN_RECUR_MINUTES > 24 * 60:
            raise ValueError("recurring jobs are disabled by config")
        self.seq += 1
        job = {
            "id": str(self.seq),
            "agent": session.cfg.name,
            "chat_id": session.chat_id,
            "thread_id": session.thread_id,
            "kind": kind,                      # remind | prompt
            "text": text[:2000],
            "next_ts": nxt.timestamp(),
            "recur": recur,        # None | daily/weekly/weekdays spec
            "until_ts": until_ts,
            "next_human": nxt.strftime("%Y-%m-%d %H:%M"),
        }
        self.jobs.append(job)
        self._save()
        return job

    def cancel(self, job_id: str) -> bool:
        before = len(self.jobs)
        self.jobs = [j for j in self.jobs if j["id"] != str(job_id).strip()]
        if len(self.jobs) != before:
            self._save()
            return True
        return False

    def list_jobs(self) -> list[dict]:
        return sorted(self.jobs, key=lambda j: j["next_ts"])

    async def _loop(self):
        while True:
            try:
                await asyncio.sleep(20)
                now = time.time()
                due = [j for j in self.jobs if j["next_ts"] <= now]
                for job in due:
                    await self._fire(job)
                if due:
                    self._save()
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("scheduler tick failed")

    async def _fire(self, job: dict):
        log.info("firing job %s (%s)", job["id"], job["kind"])
        delivered = False
        try:
            # deliver where the job was created (forum topic or private chat)
            session = await self.mgr.session_for_job(
                job["agent"], job.get("chat_id"), job.get("thread_id"))
            if job["kind"] == "remind":
                session.outbox.emit(f"⏰ reminder: {job['text']}")
            else:
                session.outbox.emit(f"⏰ scheduled prompt #{job['id']} firing")
                await session.feed(
                    f"[scheduled job #{job['id']}] {job['text']}",
                    TurnSource(kind="sched"))
            delivered = True
        except Exception:
            metrics.bump("sched_fail")
            log.exception("job %s delivery failed", job["id"])
        if not delivered and not job.get("recur"):
            # don't lose a one-shot reminder to a transient failure; back off
            # exponentially (60s, 120s, 240s) so a flapping target isn't hammered
            job["fails"] = job.get("fails", 0) + 1
            if job["fails"] <= 3:
                job["next_ts"] = time.time() + 60 * (2 ** (job["fails"] - 1))
                return
            log.error("job %s dropped after repeated delivery failures", job["id"])
            self.jobs = [j for j in self.jobs if j["id"] != job["id"]]
            return
        job.pop("fails", None)
        if job.get("recur"):
            floor = datetime.now() + timedelta(minutes=MIN_RECUR_MINUTES)
            try:
                nxt = next_fire(job["recur"], datetime.now())
                while nxt < floor:
                    nxt = next_fire(job["recur"], nxt)
            except ValueError:
                log.warning("bad recur spec on job %s; dropping", job["id"])
                self.jobs = [j for j in self.jobs if j["id"] != job["id"]]
                return
            if job.get("until_ts") and nxt.timestamp() > job["until_ts"]:
                self.jobs = [j for j in self.jobs if j["id"] != job["id"]]
                return
            job["next_ts"] = nxt.timestamp()
            job["next_human"] = nxt.strftime("%Y-%m-%d %H:%M")
        else:
            self.jobs = [j for j in self.jobs if j["id"] != job["id"]]
