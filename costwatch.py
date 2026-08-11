#!/usr/bin/env python3
"""Daily proof that the tenancy still costs nothing — and still cannot.

Three layers keep the bill at zero, and this watches all of them:

1. The fact:   month-to-date cost from the Usage API must be 0.
2. The cap:    the `zero-dollar-cap` quota policy must still exist — it is
               what makes over-provisioning fail at creation instead of
               becoming a line item (proven 2026-08-07: an FS create was
               rejected naming the policy).
3. The tail:   total block storage must stay at or under the 200GB Always
               Free ceiling, because storage is the one resource that grows
               by drift rather than by a deliberate act.

Silent when everything is green. Any finding goes to the ops bot, because a
guard that only writes to a log is a guard nobody is watching.
"""

from __future__ import annotations

import datetime
import json
import subprocess
import sys

TENANCY = (
    "ocid1.tenancy.oc1..aaaaaaaahd6mzl2lzrbqcaty54ooauccajk3k6grg7mucbstjmh6lydst4va"
)
STORAGE_CAP_GB = 200
ADS = [f"duUB:EU-FRANKFURT-1-AD-{n}" for n in (1, 2, 3)]
NOTIFY = "/home/ubuntu/alfred/opsnotify.sh"
OCI = "/home/ubuntu/.local/bin/oci"


def oci_json(*args: str) -> dict | list:
    out = subprocess.run(
        [OCI, *args], capture_output=True, text=True, timeout=120
    )
    if out.returncode != 0:
        raise RuntimeError(f"oci {args[0]} {args[1]}: {out.stderr.strip()[-200:]}")
    return json.loads(out.stdout)["data"] if out.stdout.strip() else {}


def month_to_date_cost() -> tuple[float, str]:
    today = datetime.date.today()
    start = today.replace(day=1).strftime("%Y-%m-%dT00:00:00Z")
    # The API requires end > start; on the 1st, midnight-to-midnight is empty,
    # so always reach one day forward. Future hours simply have no usage rows.
    end = (today + datetime.timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
    body = oci_json(
        "usage-api", "usage-summary", "request-summarized-usages",
        "--tenant-id", TENANCY,
        "--time-usage-started", start,
        "--time-usage-ended", end,
        "--granularity", "MONTHLY",
    )
    items = body.get("items", []) if isinstance(body, dict) else []
    total = sum(i.get("computed-amount") or 0 for i in items)
    currency = items[0].get("currency", "EUR") if items else "EUR"
    return total, currency


def block_storage_gb() -> int:
    total = 0
    for ad in ADS:
        for cmd in ("boot-volume", "volume"):
            vols = oci_json(
                "bv", f"{cmd}", "list",
                "--compartment-id", TENANCY,
                "--availability-domain", ad,
                "--all",
            )
            for v in vols or []:
                if v.get("lifecycle-state") not in ("TERMINATED", "TERMINATING"):
                    total += int(v.get("size-in-gbs") or 0)
    return total


def guards_present() -> list[str]:
    missing = []
    quotas = oci_json("limits", "quota", "list", "--compartment-id", TENANCY, "--all")
    if not any(q.get("name") == "zero-dollar-cap" for q in quotas or []):
        missing.append("quota policy `zero-dollar-cap` is GONE")
    budgets = oci_json(
        "budgets", "budget", "budget", "list", "--compartment-id", TENANCY, "--all"
    )
    if not budgets:
        missing.append("the budget is GONE")
    return missing


def main() -> int:
    problems: list[str] = []
    try:
        cost, currency = month_to_date_cost()
        if cost > 0:
            problems.append(f"tenancy has REAL SPEND this month: {cost} {currency}")
    except RuntimeError as exc:
        problems.append(f"cannot read cost: {exc}")
    try:
        gb = block_storage_gb()
        if gb > STORAGE_CAP_GB:
            problems.append(f"block storage at {gb}GB — OVER the {STORAGE_CAP_GB}GB free cap")
        elif gb > STORAGE_CAP_GB - 5:
            problems.append(f"block storage at {gb}GB — within 5GB of the free cap")
    except RuntimeError as exc:
        problems.append(f"cannot read storage: {exc}")
    try:
        problems.extend(guards_present())
    except RuntimeError as exc:
        problems.append(f"cannot verify guards: {exc}")

    if not problems:
        print("costwatch: all green")
        return 0
    text = "\N{MONEY BAG} zero-dollar budget check failed:\n" + "\n".join(
        f"- {p}" for p in problems
    )
    print(text)
    subprocess.run([NOTIFY, text], timeout=60, check=False)
    return 1


if __name__ == "__main__":
    sys.exit(main())
