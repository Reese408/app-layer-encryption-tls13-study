"""Container CPU and memory sampler using the Docker Engine API stats stream (~1 Hz).

Raw cumulative counters are recorded so CPU utilisation can be recomputed exactly over
any window during analysis:

    cpu_pct (of one core) = d(cpu_total_ns) / d(read_ns) * 100
    mem_working_set       = mem_usage - inactive_file   (what `docker stats` reports)

Timestamps come from the Docker daemon (``read``), i.e. the same VM clock the k6
container uses, so k6 measurement windows can be aligned with resource samples.
"""

from __future__ import annotations

import csv
import datetime as dt
import http.client
import json
import re
import socket
import subprocess
import threading
import time
from pathlib import Path

FIELDS = [
    "run_id",
    "container",
    "read_utc",
    "read_ns",
    "cpu_total_ns",
    "system_cpu_ns",
    "online_cpus",
    "mem_usage_bytes",
    "mem_inactive_file_bytes",
    "mem_working_set_bytes",
    "mem_limit_bytes",
    "cpu_pct_docker",
]


def docker_socket_path() -> str:
    host = subprocess.run(
        ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not host.startswith("unix://"):
        raise RuntimeError(f"unsupported docker host {host!r}")
    return host.removeprefix("unix://")


class _UnixConn(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self._path)


def parse_read_ns(ts: str) -> int:
    """Parse Docker's RFC3339Nano timestamp to integer nanoseconds since the epoch."""
    base, _, frac = ts.rstrip("Z").partition(".")
    frac = (frac + "000000000")[:9]
    secs = dt.datetime.fromisoformat(base).replace(tzinfo=dt.UTC).timestamp()
    return int(secs) * 1_000_000_000 + int(frac)


def stats_to_row(run_id: str, container: str, s: dict) -> dict | None:
    cpu, pre, mem = s.get("cpu_stats", {}), s.get("precpu_stats", {}), s.get("memory_stats", {})
    if not cpu.get("cpu_usage") or "usage" not in mem:
        return None  # container not running yet / already gone
    total = cpu["cpu_usage"]["total_usage"]
    system = cpu.get("system_cpu_usage", 0)
    online = cpu.get("online_cpus") or len(cpu["cpu_usage"].get("percpu_usage") or []) or 1
    d_cpu = total - pre.get("cpu_usage", {}).get("total_usage", 0)
    d_sys = system - pre.get("system_cpu_usage", 0)
    pct = (d_cpu / d_sys) * online * 100.0 if d_sys > 0 and pre.get("system_cpu_usage") else ""
    inactive = mem.get("stats", {}).get("inactive_file", 0)
    return {
        "run_id": run_id,
        "container": container,
        "read_utc": s["read"],
        "read_ns": parse_read_ns(s["read"]),
        "cpu_total_ns": total,
        "system_cpu_ns": system,
        "online_cpus": online,
        "mem_usage_bytes": mem["usage"],
        "mem_inactive_file_bytes": inactive,
        "mem_working_set_bytes": mem["usage"] - inactive,
        "mem_limit_bytes": mem.get("limit", ""),
        "cpu_pct_docker": f"{pct:.3f}" if pct != "" else "",
    }


class Sampler:
    """Streams stats for several containers into one CSV until stopped or containers exit."""

    def __init__(self, csv_path: Path, run_id: str, containers: list[str]) -> None:
        self.csv_path, self.run_id, self.containers = csv_path, run_id, containers
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._sock = docker_socket_path()
        self.rows_written = 0
        new = not csv_path.exists()
        self._fh = csv_path.open("a", newline="")
        self._w = csv.DictWriter(self._fh, fieldnames=FIELDS)
        if new:
            self._w.writeheader()

    def _stream(self, name: str) -> None:
        conn = _UnixConn(self._sock, timeout=3.0)
        try:
            conn.request("GET", f"/containers/{name}/stats?stream=true")
            resp = conn.getresponse()
            if resp.status != 200:
                return
            while not self._stop.is_set():
                try:
                    line = resp.readline()
                except (TimeoutError, OSError):
                    continue
                if not line:
                    break  # container exited
                row = stats_to_row(self.run_id, name, json.loads(line))
                if row:
                    with self._lock:
                        self._w.writerow(row)
                        self.rows_written += 1
        finally:
            conn.close()

    def start(self) -> Sampler:
        for c in self.containers:
            self._spawn(c)
        return self

    def add(self, container: str) -> None:
        """Start streaming one more container into the same CSV (e.g. k6, once it exists)."""
        self.containers.append(container)
        self._spawn(container)

    def _spawn(self, container: str) -> None:
        t = threading.Thread(target=self._stream, args=(container,), daemon=True)
        t.start()
        self._threads.append(t)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout)
        with self._lock:
            self._fh.flush()
            self._fh.close()


# --- Phase 4: host-side sampling of the Ollama processes and the GPU (macOS) -------------

HOST_FIELDS = [
    "run_id",
    "t_utc",
    "t_ns",
    "kind",
    "pid",
    "name",
    "rss_bytes",
    "cpu_time_s",
    "gpu_device_util_pct",
    "gpu_renderer_util_pct",
    "gpu_in_use_mem_bytes",
]
_GPU_RE = {
    "gpu_device_util_pct": re.compile(r'"Device Utilization %"=(\d+)'),
    "gpu_renderer_util_pct": re.compile(r'"Renderer Utilization %"=(\d+)'),
    "gpu_in_use_mem_bytes": re.compile(r'"In use system memory"=(\d+)'),
}


def parse_cputime(s: str) -> float:
    """macOS `ps -o time` ([[dd-]hh:]mm:ss.ss) -> seconds."""
    days, _, rest = s.strip().rpartition("-")
    secs = 0.0
    for part in rest.split(":"):
        secs = secs * 60 + float(part)
    return secs + (int(days) * 86400 if days else 0)


def ollama_processes() -> list[dict]:
    """Ollama app processes (server and model runner): pid, name, RSS, cumulative CPU time."""
    out = subprocess.run(
        ["ps", "-A", "-o", "pid=,rss=,time=,comm="], capture_output=True, text=True, check=True
    ).stdout
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4 and "/Ollama.app/" in parts[3]:
            rows.append(
                {
                    "pid": int(parts[0]),
                    "name": parts[3].rsplit("/", 1)[-1],
                    "rss_bytes": int(parts[1]) * 1024,
                    "cpu_time_s": parse_cputime(parts[2]),
                }
            )
    return rows


def gpu_counters() -> dict:
    """Driver-reported IOAccelerator PerformanceStatistics (unprivileged; semantics are not
    publicly documented, so they are reported as indicators only)."""
    out = subprocess.run(
        ["ioreg", "-r", "-d", "1", "-w", "0", "-c", "IOAccelerator"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    return {k: (int(m.group(1)) if (m := r.search(out)) else "") for k, r in _GPU_RE.items()}


class HostSampler:
    """~1 Hz host-clock samples of the Ollama processes and, optionally, the GPU counters."""

    def __init__(self, csv_path: Path, run_id: str, gpu: bool, interval_s: float = 1.0) -> None:
        self.run_id, self.gpu, self.interval_s = run_id, gpu, interval_s
        self._stop = threading.Event()
        self.rows_written = 0
        new = not csv_path.exists()
        self._fh = csv_path.open("a", newline="")
        self._w = csv.DictWriter(self._fh, fieldnames=HOST_FIELDS, restval="")
        if new:
            self._w.writeheader()
        self._t = threading.Thread(target=self._loop, daemon=True)

    def _sample(self) -> None:
        t_ns = time.time_ns()
        base = {
            "run_id": self.run_id,
            "t_utc": dt.datetime.fromtimestamp(t_ns / 1e9, dt.UTC).isoformat(),
            "t_ns": t_ns,
        }
        for p in ollama_processes():
            self._w.writerow({**base, "kind": "proc", **p})
            self.rows_written += 1
        if self.gpu:
            self._w.writerow({**base, "kind": "gpu", **gpu_counters()})
            self.rows_written += 1

    def _loop(self) -> None:
        nxt = time.monotonic()
        while not self._stop.is_set():
            self._sample()
            nxt += self.interval_s
            self._stop.wait(max(0.0, nxt - time.monotonic()))

    def start(self) -> HostSampler:
        self._t.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._t.join(5)
        self._sample()  # final reading, so CPU time covers the whole run
        self._fh.flush()
        self._fh.close()
