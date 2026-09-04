"""Durable Manager Loop orchestration for long-running Alfred tasks.

Purpose:  Plan, implement, independently verify, and integrate multi-phase tasks.
Inputs:   A user goal, owner agent, working directory, and Claude worker sessions.
Outputs:  Persistent run records plus concise progress notifications.
Key fns:  ManagerLoop.start, resume, cancel, summaries; parse_json_object.
Deps:     config.save_json/load_json and AgentManager session lifecycle.
Note:     Telegram is only a view; state/manager-loops.json is the source of truth.
Updated:  2026-09-04
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .config import LOOPS_FILE, load_json, save_json
from .session import AgentConfig

log = logging.getLogger("bridge.manager_loop")

Notify = Callable[[str], Awaitable[None]]
TERMINAL = {"complete", "failed", "cancelled"}


def parse_json_object(text: str) -> dict[str, Any]:
    """Extract one JSON object from a model response, tolerating code fences."""
    raw = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw,
                       re.IGNORECASE | re.DOTALL)
    candidates = [fenced.group(1)] if fenced else []
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start:end + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
        except (TypeError, ValueError):
            continue
    raise ValueError("agent did not return a valid JSON object")


def normalize_phases(payload: dict[str, Any], max_phases: int = 12) -> list[dict]:
    """Validate and normalize a manager-created phase plan."""
    raw = payload.get("phases")
    if not isinstance(raw, list) or not raw:
        raise ValueError("plan must contain a non-empty phases array")
    phases = []
    seen: set[str] = set()
    for pos, item in enumerate(raw[:max_phases], 1):
        if not isinstance(item, dict):
            raise ValueError(f"phase {pos} is not an object")
        pid = str(item.get("id") or pos).strip()[:24]
        objective = str(item.get("objective") or "").strip()
        acceptance = item.get("acceptance_criteria") or item.get("acceptance")
        if isinstance(acceptance, str):
            acceptance = [acceptance]
        if not objective or not isinstance(acceptance, list) or not acceptance:
            raise ValueError(f"phase {pid} needs objective and acceptance criteria")
        if pid in seen:
            raise ValueError(f"duplicate phase id: {pid}")
        deps = item.get("dependencies") or []
        if isinstance(deps, str):
            deps = [deps]
        criteria = [str(x)[:1000] for x in acceptance if str(x).strip()]
        if not criteria:
            raise ValueError(f"phase {pid} has empty acceptance criteria")
        phases.append({
            "id": pid, "objective": objective[:2000],
            "dependencies": [str(x)[:24] for x in deps],
            "acceptance_criteria": criteria, "status": "pending",
            "attempts": 0, "evidence": [],
        })
        seen.add(pid)
    prior: set[str] = set()
    for phase in phases:
        unknown = set(phase["dependencies"]) - seen
        if unknown:
            raise ValueError(f"phase {phase['id']} has unknown dependencies: {unknown}")
        forward = set(phase["dependencies"]) - prior
        if forward:
            raise ValueError(f"phase {phase['id']} depends on a later phase: {forward}")
        prior.add(phase["id"])
    return phases


class ManagerLoop:
    """Persistent orchestration state machine owned by AgentManager."""

    def __init__(self, manager, state_file: Path = LOOPS_FILE):
        self.manager = manager
        self.state_file = state_file
        raw = load_json(state_file, {"runs": {}})
        self.runs: dict[str, dict] = raw.get("runs", {})
        self.tasks: dict[str, asyncio.Task] = {}
        changed = False
        for run in self.runs.values():
            if run.get("status") in {"planning", "running", "integrating"}:
                run["status"] = "paused"
                run["last_error"] = "bridge restarted; explicit resume required"
                changed = True
        if changed:
            self._save()

    def _save(self) -> None:
        save_json(self.state_file, {"version": 1, "runs": self.runs})

    def _active_in(self, workdir: str, except_id: str = "") -> bool:
        return any(rid != except_id and r.get("workdir") == workdir
                   and r.get("status") not in TERMINAL | {"paused"}
                   for rid, r in self.runs.items())

    def start(self, goal: str, owner: str, workdir: str, notify: Notify) -> str:
        goal = goal.strip()
        if not goal:
            raise ValueError("goal is required")
        if self._active_in(workdir):
            raise ValueError("another Manager Loop is already editing this working directory")
        rid = uuid.uuid4().hex[:8]
        now = int(time.time())
        self.runs[rid] = {
            "id": rid, "goal": goal, "owner": owner, "workdir": workdir,
            "status": "planning", "created_at": now, "updated_at": now,
            "phases": [], "current_phase": None, "last_error": "",
            "final_evidence": [],
        }
        self._save()
        self._launch(rid, notify)
        return rid

    def resume(self, rid: str, notify: Notify) -> None:
        run = self.runs.get(rid)
        if not run:
            raise ValueError("unknown loop id")
        if run.get("status") in TERMINAL:
            raise ValueError(f"loop is already {run['status']}")
        if rid in self.tasks and not self.tasks[rid].done():
            raise ValueError("loop is already running")
        if self._active_in(run["workdir"], except_id=rid):
            raise ValueError("another Manager Loop is already editing this working directory")
        run["status"] = "running" if run.get("phases") else "planning"
        run["last_error"] = ""
        self._save()
        self._launch(rid, notify)

    def cancel(self, rid: str) -> bool:
        run = self.runs.get(rid)
        if not run or run.get("status") in TERMINAL:
            return False
        task = self.tasks.get(rid)
        if task and not task.done():
            task.cancel()
        run["status"] = "cancelled"
        self._touch(run)
        return True

    def summaries(self) -> list[str]:
        rows = []
        recent = sorted(self.runs.values(), key=lambda r: r.get("created_at", 0),
                        reverse=True)[:10]
        for run in recent:
            phases = run.get("phases", [])
            done = sum(p.get("status") == "verified" for p in phases)
            rows.append(f"{run['id']} · {run['status']} · {done}/{len(phases)} · "
                        f"{run['goal'][:90]}")
        return rows

    async def stop(self) -> None:
        live = [task for task in self.tasks.values() if not task.done()]
        for task in live:
            task.cancel()
        if live:
            await asyncio.gather(*live, return_exceptions=True)

    def _launch(self, rid: str, notify: Notify) -> None:
        task = asyncio.create_task(self._run(rid, notify))
        self.tasks[rid] = task
        task.add_done_callback(lambda _task, run_id=rid:
                               self.tasks.pop(run_id, None))

    async def _worker(self, run: dict, role: str):
        name = f"loop-{role}-{run['id'][:6]}"[:20]
        if name not in self.manager.agents:
            base = self.manager.agents.get(run["owner"]) or self.manager.agents["main"]
            read_only = role in {"mgr", "verify", "int"}
            self.manager.agents[name] = AgentConfig(
                name=name, workdir=run["workdir"], model=base.model,
                auto_approve=False if read_only else base.auto_approve,
                always_allow=[] if read_only else list(base.always_allow),
                read_only=read_only)
            self.manager.save_agents()
        return await self.manager.session_for_agent(name)

    async def _run(self, rid: str, notify: Notify) -> None:
        run = self.runs[rid]
        try:
            if not run["phases"]:
                await notify(f"🧭 loop {rid}: manager is building the first plan…")
                manager = await self._worker(run, "mgr")
                result = await manager.collect(self._plan_prompt(run), timeout=600)
                run["phases"] = normalize_phases(parse_json_object(result))
                run["status"] = "running"
                self._touch(run)
                await notify(f"🗺 loop {rid}: plan ready — {len(run['phases'])} phases")

            for phase in run["phases"]:
                if phase["status"] == "verified":
                    continue
                unmet = [dep for dep in phase["dependencies"]
                         if not self._verified(run, dep)]
                if unmet:
                    raise RuntimeError(
                        f"phase {phase['id']} has unmet dependencies: {unmet}")
                await self._execute_phase(run, phase, notify)

            run["status"] = "integrating"
            self._touch(run)
            await notify(f"🧩 loop {rid}: all phases verified; integration review…")
            integrator = await self._worker(run, "int")
            result = await integrator.collect(self._integration_prompt(run), timeout=900)
            verdict = parse_json_object(result)
            if str(verdict.get("verdict", "")).upper() != "PASS":
                raise RuntimeError("integration review failed: " +
                                   str(verdict.get("summary") or verdict)[:1000])
            final_evidence = verdict.get("evidence") or []
            if isinstance(final_evidence, str):
                final_evidence = [final_evidence]
            run["final_evidence"] = [str(x)[:1000] for x in final_evidence]
            run["status"] = "complete"
            run["current_phase"] = None
            self._touch(run)
            await notify(f"✅ loop {rid} complete: "
                         f"{verdict.get('summary', run['goal'])}")
        except asyncio.CancelledError:
            if run.get("status") != "cancelled":
                run["status"] = "paused"
                run["last_error"] = "execution interrupted"
                self._touch(run)
            raise
        except Exception as exc:
            log.exception("manager loop %s paused", rid)
            run["status"] = "paused"
            run["last_error"] = str(exc)[:2000]
            self._touch(run)
            await notify(f"⏸ loop {rid} paused: {str(exc)[:700]}\n"
                         f"Use /loop resume {rid} after resolving the blocker.")

    async def _execute_phase(self, run: dict, phase: dict,
                             notify: Notify) -> None:
        implementer = await self._worker(run, "impl")
        verifier = await self._worker(run, "verify")
        run["current_phase"] = phase["id"]
        for _ in range(2):
            phase["attempts"] += 1
            phase["status"] = "implementing"
            self._touch(run)
            await notify(f"🔨 loop {run['id']} · phase {phase['id']} "
                         f"(attempt {phase['attempts']}): {phase['objective'][:180]}")
            implementation = await implementer.collect(
                self._implementation_prompt(run, phase), timeout=1800)
            phase["status"] = "verifying"
            self._touch(run)
            await notify(f"🔎 loop {run['id']} · phase {phase['id']}: verification…")
            checked = await verifier.collect(
                self._verification_prompt(run, phase, implementation), timeout=900)
            verdict = parse_json_object(checked)
            evidence = verdict.get("evidence") or []
            if isinstance(evidence, str):
                evidence = [evidence]
            phase["evidence"] = [str(item)[:1000] for item in evidence]
            decision = str(verdict.get("verdict", "")).upper()
            if decision == "PASS":
                phase["status"] = "verified"
                self._touch(run)
                await notify(f"✅ loop {run['id']} · phase {phase['id']} verified")
                return
            if decision == "BLOCKED":
                phase["status"] = "blocked"
                phase["feedback"] = str(verdict.get("feedback") or verdict)[:3000]
                self._touch(run)
                raise RuntimeError(f"phase {phase['id']} is blocked: "
                                   f"{phase['feedback']}")
            phase["status"] = "revision_required"
            phase["feedback"] = str(verdict.get("feedback") or verdict)[:3000]
            self._touch(run)
        phase["status"] = "failed"
        self._touch(run)
        raise RuntimeError(f"phase {phase['id']} failed verification twice: "
                           f"{phase.get('feedback', 'no feedback')}")

    def _touch(self, run: dict) -> None:
        run["updated_at"] = int(time.time())
        self._save()

    @staticmethod
    def _verified(run: dict, pid: str) -> bool:
        return any(p["id"] == pid and p["status"] == "verified"
                   for p in run["phases"])

    @staticmethod
    def _plan_prompt(run: dict) -> str:
        return f"""You are the MANAGER for a long-horizon task in {run['workdir']}.
Goal: {run['goal']}

Inspect the repository before planning. Produce a small dependency-ordered plan
of independently verifiable phases. Do not edit files. Return ONLY JSON:
{{"phases":[{{"id":"p1","objective":"...","dependencies":[],
"acceptance_criteria":["observable criterion", "exact test/check"]}}]}}
Use 2-8 phases normally. Criteria must be measurable; avoid words like good,
complete, robust, or polished unless followed by an observable definition."""

    @staticmethod
    def _implementation_prompt(run: dict, phase: dict) -> str:
        previous = phase.get("feedback", "")
        return f"""You are the IMPLEMENTER for one phase of a controlled task.
Overall goal: {run['goal']}
Phase {phase['id']}: {phase['objective']}
Acceptance criteria: {json.dumps(phase['acceptance_criteria'], ensure_ascii=False)}
Verifier feedback from the prior attempt: {previous or '(none)'}

Inspect current repository state, implement only this phase, and run proportionate
checks. Preserve unrelated user changes. Do not deploy, push, purchase, message
third parties, or perform destructive/irreversible actions. End with a concise
report listing changed artifacts, checks run, results, and remaining risks."""

    @staticmethod
    def _verification_prompt(run: dict, phase: dict, report: str) -> str:
        return f"""You are an INDEPENDENT VERIFIER. Do not trust the implementer's
self-report. Inspect the actual files/diff and run the checks needed to evaluate
every acceptance criterion. Do not modify implementation files.

Overall goal: {run['goal']}
Phase: {phase['objective']}
Acceptance criteria: {json.dumps(phase['acceptance_criteria'], ensure_ascii=False)}
Implementer report (untrusted): {report[-6000:]}

Return ONLY JSON:
{{"verdict":"PASS|REVISE|BLOCKED","evidence":["command/result or artifact"],
"feedback":"specific corrective work","summary":"one sentence"}}
PASS only when every criterion has direct evidence."""

    @staticmethod
    def _integration_prompt(run: dict) -> str:
        summary = [{"id": p["id"], "objective": p["objective"],
                    "evidence": p["evidence"]} for p in run["phases"]]
        return f"""You are the final INTEGRATOR for this repository task.
Goal: {run['goal']}
Verified phase records: {json.dumps(summary, ensure_ascii=False)[:12000]}

Inspect the combined repository state and run system-level checks. Do not modify
files. Confirm the phases work together and no acceptance criterion regressed.
Return ONLY JSON:
{{"verdict":"PASS|REVISE|BLOCKED","evidence":["check/result"],
"summary":"concise final outcome and any residual risk"}}"""
