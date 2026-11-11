"""Infra Health Monitor: a small FastAPI service that reports host health.

Endpoints
  GET /health   liveness probe (used by Docker HEALTHCHECK and load balancers)
  GET /metrics  CPU, memory, disk, load and threshold-based alerts
  GET /info     host and runtime details
"""
import os
import platform
import socket
import time

import psutil
from fastapi import FastAPI

START_TIME = time.time()

app = FastAPI(title="Infra Health Monitor", version="1.0.0")


def _threshold(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def build_alerts(cpu: float, mem: float, disk: float) -> list[str]:
    """Return a list of alert strings for metrics above their thresholds."""
    limits = {
        "cpu": (cpu, _threshold("CPU_ALERT_PERCENT", 85)),
        "memory": (mem, _threshold("MEM_ALERT_PERCENT", 85)),
        "disk": (disk, _threshold("DISK_ALERT_PERCENT", 90)),
    }
    return [
        f"{name} usage {value:.1f}% exceeds {limit:.0f}%"
        for name, (value, limit) in limits.items()
        if value > limit
    ]


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "uptime_seconds": round(time.time() - START_TIME, 1)}


@app.get("/metrics")
def metrics() -> dict:
    cpu = psutil.cpu_percent(interval=0.2)
    vm = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    load1, load5, load15 = os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)
    return {
        "cpu_percent": cpu,
        "memory_percent": vm.percent,
        "memory_used_mb": round(vm.used / 1024**2, 1),
        "memory_total_mb": round(vm.total / 1024**2, 1),
        "disk_percent": disk.percent,
        "disk_free_gb": round(disk.free / 1024**3, 2),
        "load_avg": {"1m": load1, "5m": load5, "15m": load15},
        "alerts": build_alerts(cpu, vm.percent, disk.percent),
    }


@app.get("/info")
def info() -> dict:
    return {
        "hostname": socket.gethostname(),
        "os": platform.platform(),
        "python": platform.python_version(),
        "cpu_count": psutil.cpu_count(),
    }
