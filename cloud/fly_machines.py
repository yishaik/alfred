"""Optional ephemeral Fly Machine jobs for isolation from the Telegram control plane."""
from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass

import httpx

from .security import PolicyError, validate_repo


@dataclass(frozen=True)
class WorkerJob:
    repo: str
    ref: str
    prompt: str
    task_id: str

    def payload(self) -> str:
        validate_repo(self.repo)
        if len(self.prompt) > 20000:
            raise PolicyError("worker prompt exceeds 20,000 characters")
        raw = json.dumps(self.__dict__, ensure_ascii=False).encode()
        return base64.urlsafe_b64encode(raw).decode()


class FlyMachinesClient:
    def __init__(self) -> None:
        self.token = os.environ.get("FLY_API_TOKEN", "")
        self.app = os.environ.get("ALFRED_WORKER_APP", "")
        self.image = os.environ.get("ALFRED_WORKER_IMAGE", "")
        if not all((self.token, self.app, self.image)):
            raise PolicyError("FLY_API_TOKEN, ALFRED_WORKER_APP and ALFRED_WORKER_IMAGE are required")
        self.base = "https://api.machines.dev/v1"
        self.headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def create(self, job: WorkerJob) -> dict:
        body = {"name": f"job-{job.task_id[:20].lower()}", "region": os.environ.get("PRIMARY_REGION", "fra"),
                "config": {"image": self.image, "auto_destroy": True, "restart": {"policy": "no"},
                           "guest": {"cpu_kind": "shared", "cpus": 2, "memory_mb": 2048},
                           "env": {"ALFRED_JOB_SPEC_B64": job.payload(), "ALFRED_JOB_MODE": "isolated-worker"},
                           "init": {"cmd": ["python", "-m", "cloud.worker"]},
                           "metadata": {"managed-by": "alfred", "task-id": job.task_id}}}
        response = httpx.post(f"{self.base}/apps/{self.app}/machines", headers=self.headers, json=body, timeout=60)
        response.raise_for_status()
        return response.json()

    def wait(self, machine_id: str, timeout: int = 1800) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            response = httpx.get(f"{self.base}/apps/{self.app}/machines/{machine_id}", headers=self.headers, timeout=30)
            response.raise_for_status()
            data = response.json()
            if data.get("state") in {"stopped", "destroyed", "failed"}: return data
            time.sleep(3)
        raise TimeoutError(f"worker {machine_id} did not finish in {timeout}s")
