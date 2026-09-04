"""Offline tests for the durable Manager Loop state machine."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from tgbridge.manager_loop import ManagerLoop, normalize_phases, parse_json_object
from tgbridge.session import AgentConfig


class FakeSession:
    def __init__(self, role: str):
        self.role = role

    async def collect(self, _prompt: str, timeout: float = 0) -> str:
        if "-mgr-" in self.role:
            return json.dumps({"phases": [{
                "id": "p1", "objective": "make the change", "dependencies": [],
                "acceptance_criteria": ["test command passes"],
            }]})
        if "-verify-" in self.role:
            return '{"verdict":"PASS","evidence":["test: pass"],"summary":"ok"}'
        if "-int-" in self.role:
            return '{"verdict":"PASS","evidence":["suite: pass"],"summary":"integrated"}'
        return "changed file.py; test command passes"


class FakeManager:
    def __init__(self):
        self.agents = {"main": AgentConfig(name="main", workdir="/tmp/work")}

    def save_agents(self):
        return None

    async def session_for_agent(self, name: str):
        return FakeSession(name)


class ManagerLoopTests(unittest.TestCase):
    def test_json_and_plan_normalization(self):
        payload = parse_json_object(
            'Here: ```json\n{"phases":[{"id":"p1","objective":"x",'
            '"acceptance":["y"]}]}\n```')
        phases = normalize_phases(payload)
        self.assertEqual(phases[0]["status"], "pending")
        self.assertEqual(phases[0]["acceptance_criteria"], ["y"])

    def test_unknown_dependency_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown dependencies"):
            normalize_phases({"phases": [{
                "id": "p1", "objective": "x", "dependencies": ["missing"],
                "acceptance_criteria": ["y"],
            }]})

    def test_forward_dependency_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "later phase"):
            normalize_phases({"phases": [
                {"id": "p1", "objective": "x", "dependencies": ["p2"],
                 "acceptance_criteria": ["x passes"]},
                {"id": "p2", "objective": "y", "dependencies": [],
                 "acceptance_criteria": ["y passes"]},
            ]})

    def test_orchestration_role_is_read_only(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                loop = ManagerLoop(FakeManager(), Path(tmp) / "loops.json")
                run = {"id": "12345678", "owner": "main", "workdir": "/tmp/work"}
                await loop._worker(run, "verify")
                self.assertTrue(loop.manager.agents["loop-verify-123456"].read_only)
                self.assertFalse(loop.manager.agents["loop-verify-123456"].auto_approve)
        asyncio.run(scenario())

    def test_restart_pauses_running_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "loops.json"
            state.write_text(json.dumps({"runs": {"abc": {
                "id": "abc", "status": "running", "workdir": "/tmp/work",
            }}}), encoding="utf-8")
            loop = ManagerLoop(FakeManager(), state)
            self.assertEqual(loop.runs["abc"]["status"], "paused")

    def test_full_evidence_gated_run(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                notices = []

                async def notify(text: str):
                    notices.append(text)

                loop = ManagerLoop(FakeManager(), Path(tmp) / "loops.json")
                rid = loop.start("ship feature", "main", "/tmp/work", notify)
                await loop.tasks[rid]
                self.assertEqual(loop.runs[rid]["status"], "complete")
                self.assertEqual(loop.runs[rid]["phases"][0]["status"], "verified")
                self.assertEqual(loop.runs[rid]["final_evidence"], ["suite: pass"])
                self.assertTrue(any("complete" in item for item in notices))

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
