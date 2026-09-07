#!/usr/bin/env python3
"""
fleet_probe.py — emit this machine's node card for the zoltar-ranks fleet registry.

Run once per machine (and again whenever hardware, roles or tooling change):

    python dashboard\\fleet_probe.py --write
    python dashboard\\fleet_probe.py --heartbeat        # cheap liveness stamp
    python dashboard\\fleet_probe.py --check            # print, write nothing

Writes:
    data/fleet/nodes/<node_id>.json        capability card  (committed to git)
    data/fleet/heartbeat/<node_id>.json    liveness stamp   (committed to git)

Doctrine (same as the build monitor): DECLARED capability is intent, the
heartbeat is evidence. A node card never makes a node available; only a fresh
heartbeat does. Nothing here imports zoltar_ranks or opens DuckDB, so this
cannot fail a harvest.

Stdlib only. psutil is used if present, never required.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROBE_VERSION = 1
SCHEMA_VERSION = 1

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def run(cmd: list[str], timeout: int = 20) -> str:
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return (out.stdout or "").strip()
    except Exception:
        return ""


def ps(script: str, timeout: int = 30) -> str:
    """Run a PowerShell snippet on Windows; empty string elsewhere/on failure."""
    if platform.system() != "Windows":
        return ""
    return run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        timeout=timeout,
    )


def ps_json(script: str, timeout: int = 30):
    raw = ps(script + " | ConvertTo-Json -Depth 4 -Compress", timeout=timeout)
    if not raw:
        return None
    try:
        val = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return val


def gb(n) -> float | None:
    try:
        return round(float(n) / (1024**3), 1)
    except (TypeError, ValueError):
        return None


def repo_root(start: Path) -> Path:
    p = start.resolve()
    for cand in [p, *p.parents]:
        if (cand / ".git").exists():
            return cand
    return p


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


# --------------------------------------------------------------------------- #
# probes
# --------------------------------------------------------------------------- #


def probe_cpu() -> dict:
    d = {
        "model": platform.processor() or None,
        "physical_cores": None,
        "logical_cores": os.cpu_count(),
        "max_clock_mhz": None,
        "passmark": None,  # filled in by hand in the node card; benchmarks are not probeable
    }
    info = ps_json(
        "Get-CimInstance Win32_Processor | "
        "Select-Object Name,NumberOfCores,NumberOfLogicalProcessors,MaxClockSpeed"
    )
    if isinstance(info, list):
        info = info[0] if info else None
    if isinstance(info, dict):
        d["model"] = (info.get("Name") or d["model"] or "").strip() or None
        d["physical_cores"] = info.get("NumberOfCores")
        d["logical_cores"] = info.get("NumberOfLogicalProcessors") or d["logical_cores"]
        d["max_clock_mhz"] = info.get("MaxClockSpeed")
    elif Path("/proc/cpuinfo").exists():
        txt = Path("/proc/cpuinfo").read_text(errors="ignore")
        m = re.search(r"^model name\s*:\s*(.+)$", txt, re.M)
        if m:
            d["model"] = m.group(1).strip()
    return d


def probe_memory() -> dict:
    total = free = None
    try:
        import psutil  # type: ignore

        vm = psutil.virtual_memory()
        total, free = gb(vm.total), gb(vm.available)
    except Exception:
        info = ps_json(
            "Get-CimInstance Win32_OperatingSystem | "
            "Select-Object TotalVisibleMemorySize,FreePhysicalMemory"
        )
        if isinstance(info, dict):
            total = round((info.get("TotalVisibleMemorySize") or 0) / 1048576, 1) or None
            free = round((info.get("FreePhysicalMemory") or 0) / 1048576, 1) or None
        elif Path("/proc/meminfo").exists():
            txt = Path("/proc/meminfo").read_text(errors="ignore")
            def kb(key):
                m = re.search(rf"^{key}:\s+(\d+) kB$", txt, re.M)
                return round(int(m.group(1)) / 1048576, 1) if m else None
            total, free = kb("MemTotal"), kb("MemAvailable")

    slots = []
    mods = ps_json(
        "Get-CimInstance Win32_PhysicalMemory | "
        "Select-Object Capacity,Speed,DeviceLocator,Manufacturer"
    )
    if isinstance(mods, dict):
        mods = [mods]
    for m in mods or []:
        slots.append(
            {
                "gb": gb(m.get("Capacity")),
                "speed_mhz": m.get("Speed"),
                "slot": m.get("DeviceLocator"),
                "vendor": (m.get("Manufacturer") or "").strip() or None,
            }
        )
    return {"total_gb": total, "free_gb": free, "modules": slots or None}


def probe_disks() -> list[dict]:
    disks = []

    # Physical media: model + bus type (NVMe vs SATA matters for DuckDB/Parquet work)
    media = ps_json(
        "Get-PhysicalDisk | Select-Object FriendlyName,MediaType,BusType,Size,DeviceId"
    )
    if isinstance(media, dict):
        media = [media]
    for m in media or []:
        disks.append(
            {
                "kind": "physical",
                "model": (m.get("FriendlyName") or "").strip() or None,
                "media_type": m.get("MediaType"),
                "bus": m.get("BusType"),  # 17 = NVMe, 11 = SATA
                "total_gb": gb(m.get("Size")),
            }
        )

    # Mounted volumes: free space is what actually gates a workload
    for part in _mounts():
        try:
            usage = shutil.disk_usage(part)
        except OSError:
            continue
        disks.append(
            {
                "kind": "volume",
                "mount": str(part),
                "total_gb": gb(usage.total),
                "free_gb": gb(usage.free),
            }
        )
    return disks


def _mounts() -> list[str]:
    if platform.system() == "Windows":
        out = []
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            root = f"{letter}:\\"
            if os.path.exists(root):
                out.append(root)
        return out
    return ["/"]


def probe_gpu() -> list[dict]:
    gpus = []
    info = ps_json(
        "Get-CimInstance Win32_VideoController | "
        "Select-Object Name,AdapterRAM,DriverVersion"
    )
    if isinstance(info, dict):
        info = [info]
    for g in info or []:
        gpus.append(
            {
                "name": (g.get("Name") or "").strip() or None,
                "vram_gb": gb(g.get("AdapterRAM")),
                "driver": g.get("DriverVersion"),
            }
        )
    nv = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"])
    if nv:
        for line in nv.splitlines():
            name, _, mem = line.partition(",")
            gpus.append({"name": name.strip(), "vram": mem.strip(), "cuda": True})
    return gpus


def probe_software() -> dict:
    def ver(cmd: list[str]) -> str | None:
        out = run(cmd, timeout=15)
        return out.splitlines()[0].strip() if out else None

    mods = {}
    for mod in ("duckdb", "pandas", "pyarrow", "numpy", "psutil", "yaml", "requests"):
        try:
            m = __import__(mod)
            mods[mod] = getattr(m, "__version__", "present")
        except Exception:
            mods[mod] = None

    return {
        "python": sys.version.split()[0],
        "python_exe": sys.executable,
        "git": ver(["git", "--version"]),
        "node": ver(["node", "--version"]),
        "modules": mods,
    }


def probe_repo(root: Path) -> dict:
    def g(*args) -> str | None:
        out = run(["git", "-C", str(root), *args])
        return out or None

    return {
        "path": str(root),
        "remote": g("remote", "get-url", "origin"),
        "branch": g("rev-parse", "--abbrev-ref", "HEAD"),
        "head_sha": g("rev-parse", "--short", "HEAD"),
        "head_at": g("log", "-1", "--pretty=%cI"),
        "dirty": bool(g("status", "--porcelain")),
    }


def probe_power() -> dict:
    bat = ps_json(
        "Get-CimInstance Win32_Battery | Select-Object BatteryStatus,EstimatedChargeRemaining"
    )
    if isinstance(bat, list):
        bat = bat[0] if bat else None
    if not isinstance(bat, dict):
        return {"has_battery": False, "on_ac": None, "charge_pct": None}
    return {
        "has_battery": True,
        # BatteryStatus 2 == plugged in / AC
        "on_ac": bat.get("BatteryStatus") == 2,
        "charge_pct": bat.get("EstimatedChargeRemaining"),
    }


def probe_load() -> dict:
    d = {"cpu_pct": None, "ram_free_gb": probe_memory().get("free_gb")}
    try:
        import psutil  # type: ignore

        d["cpu_pct"] = psutil.cpu_percent(interval=0.5)
    except Exception:
        pass
    return d


# --------------------------------------------------------------------------- #
# capability tags — what the router actually reads
# --------------------------------------------------------------------------- #


def derive_tags(card: dict) -> list[str]:
    tags: list[str] = []
    ram = (card.get("memory") or {}).get("total_gb") or 0
    cores = (card.get("cpu") or {}).get("logical_cores") or 0
    disks = card.get("disks") or []
    free = max([d.get("free_gb") or 0 for d in disks if d.get("kind") == "volume"] or [0])
    nvme = any(str(d.get("bus")) == "17" for d in disks)
    gpus = card.get("gpu") or []

    if ram >= 30:
        tags.append("ram-32g+")
    if cores >= 12:
        tags.append("cores-12+")
    elif cores >= 8:
        tags.append("cores-8+")
    if nvme:
        tags.append("nvme")
    if free >= 200:
        tags.append("disk-200g+")
    if any(g.get("cuda") for g in gpus):
        tags.append("cuda")
    if (card.get("repo") or {}).get("head_sha"):
        tags.append("has-repo")
    if (card.get("software") or {}).get("modules", {}).get("duckdb"):
        tags.append("duckdb")
    if (card.get("power") or {}).get("on_ac"):
        tags.append("on-ac")
    return tags


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #


def build_card(root: Path, args) -> dict:
    host = socket.gethostname()
    node_id = args.node_id or slug(host)
    card = {
        "schema_version": SCHEMA_VERSION,
        "probe_version": PROBE_VERSION,
        "node_id": node_id,
        "hostname": host,
        "alias": args.alias,
        "probed_at": now_iso(),
        "os": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "arch": platform.machine(),
        },
        "cpu": probe_cpu(),
        "memory": probe_memory(),
        "disks": probe_disks(),
        "gpu": probe_gpu(),
        "software": probe_software(),
        "repo": probe_repo(root),
        "power": probe_power(),
        # declared, not probeable — edit in the file or pass on the CLI
        "declared": {
            "roles": args.roles.split(",") if args.roles else [],
            "always_on": args.always_on,
            "owner": "andrew",
            "notes": args.note,
        },
    }
    card["tags"] = derive_tags(card)
    return card


def build_heartbeat(root: Path, node_id: str, agents: str | None) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "node_id": node_id,
        "at": now_iso(),
        "load": probe_load(),
        "power": probe_power(),
        "repo": {k: probe_repo(root).get(k) for k in ("branch", "head_sha", "dirty")},
        "agents": [a for a in (agents or "").split(",") if a],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="write the node card")
    ap.add_argument("--heartbeat", action="store_true", help="write the liveness stamp")
    ap.add_argument("--check", action="store_true", help="print, write nothing")
    ap.add_argument("--node-id", help="stable id; defaults to slugified hostname")
    ap.add_argument("--alias", help="human name, e.g. 'E16 Gen 2'")
    ap.add_argument("--roles", help="comma list: orchestrator,backtest,harvest,dashboard,store")
    ap.add_argument("--always-on", action="store_true")
    ap.add_argument("--note", help="free text for the node card")
    ap.add_argument("--agents", help="comma list of agent ids alive on this node")
    ap.add_argument("--root", help="repo root; defaults to the git root above this file")
    args = ap.parse_args()

    root = Path(args.root) if args.root else repo_root(Path(__file__).parent)
    node_id = args.node_id or slug(socket.gethostname())

    if args.heartbeat:
        payload = build_heartbeat(root, node_id, args.agents)
        target = root / "data" / "fleet" / "heartbeat" / f"{node_id}.json"
    else:
        payload = build_card(root, args)
        target = root / "data" / "fleet" / "nodes" / f"{node_id}.json"

    text = json.dumps(payload, indent=2, sort_keys=False)
    if args.check or not (args.write or args.heartbeat):
        print(text)
        return 0

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text + "\n", encoding="utf-8")
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
