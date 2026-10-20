"""Run a benchmark profile from experiment.toml and write raw results.

Output: results/raw/<UTC timestamp>-<profile>/
  manifest.json            environment, versions, config, fixture hashes, run order
  runs.csv                 one row per run (ids, restart/readiness/k6 timestamps, container
                           identities, k6 exit code, sampler row count, sample count)
  <run_id>.summary.json    k6 end-of-test summary (exact percentiles over measured requests)
  <run_id>.k6.log          k6 stdout/stderr, excluding per-request sample lines
  <run_id>.samples.csv     deterministic 1-in-K sample of measured requests (if enabled)
  resources.csv            ~1 Hz Docker stats for the API under test and the k6 container
  client_crypto_bench.json client-side WebCrypto cost per operation (once per session)

Per-run protocol when `restart_per_run` is set (approved after the pilot, R1):
  1. recreate both API containers and wait until Docker reports them healthy;
  2. probe /healthz over TLS 1.3 from the host (certificate verified);
  3. start the resource sampler and record an idle baseline (`idle_baseline_s`);
  4. start k6, which runs its own warm-up before the measurement window.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import http.client
import itertools
import json
import os
import platform
import random
import re
import ssl
import subprocess
import sys
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from sample_resources import Sampler  # noqa: E402

API_SERVICE = {"plain": "api-plain", "aead": "api-aead"}
API_CONTAINER = {"plain": "aead-study-api-plain-1", "aead": "aead-study-api-aead-1"}
API_HOST_PORT = {"plain": 8443, "aead": 8444}

# Column order of the `S,` lines written by k6/bench.js.
SAMPLE_FIELDS = [
    "iter",
    "vu",
    "t_ms",
    "status",
    "integrity",
    "req_ms",
    "waiting_ms",
    "sending_ms",
    "receiving_ms",
    "tls_handshake_ms",
    "client_enc_ms",
    "client_dec_ms",
    "srv_dec_ms",
    "srv_work_ms",
    "srv_enc_ms",
    "srv_app_ms",
    "iter_ms",
]
_SAMPLE_RE = re.compile(r'msg="S,([^"]*)"')


def sh(*cmd: str, check: bool = True) -> str:
    return subprocess.run(cmd, cwd=ROOT, check=check, capture_output=True, text=True).stdout.strip()


def utcnow() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def power_source() -> str:
    """'AC Power' or 'Battery Power' on macOS (first line of `pmset -g batt`), else ''."""
    out = sh("pmset", "-g", "batt", check=False)
    m = re.search(r"'([^']+)'", out.splitlines()[0]) if out else None
    return m.group(1) if m else ""


def write_runs(path: Path, rows: list[dict]) -> None:
    """Rewrite runs.csv after every run, so an interrupted session keeps its run index."""
    fields: list[str] = []
    for r in rows:
        fields += [k for k in r if k not in fields]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, restval="")
        w.writeheader()
        w.writerows(rows)


def split_k6_log(text: str) -> tuple[str, list[list[str]]]:
    """Separate per-request sample lines from the rest of a k6 log."""
    other, rows = [], []
    for line in text.splitlines():
        m = _SAMPLE_RE.search(line)
        if m:
            fields = m.group(1).split(",")
            if len(fields) != len(SAMPLE_FIELDS):
                raise ValueError(
                    f"sample line has {len(fields)} fields, expected {len(SAMPLE_FIELDS)}"
                )
            rows.append(fields)
        else:
            other.append(line)
    return "\n".join(other) + ("\n" if other else ""), rows


def manifest(profile: str, cfg: dict, prof: dict, order: list[dict]) -> dict:
    api_image = sh("docker", "image", "inspect", "aead-study-api:local", "--format", "{{.Id}}")
    return {
        "profile": profile,
        "started_utc": utcnow(),
        "git": {
            "commit": sh("git", "rev-parse", "HEAD"),
            "dirty": bool(sh("git", "status", "--porcelain", "--untracked-files=no")),
        },
        "config": {**{k: v for k, v in cfg.items() if k != "profiles"}, "profile": prof},
        "run_order": order,
        "host": {
            "os": f"{sh('sw_vers', '-productName')} {sh('sw_vers', '-productVersion')}",
            "cpu": sh("sysctl", "-n", "machdep.cpu.brand_string"),
            "ncpu": int(sh("sysctl", "-n", "hw.ncpu")),
            "mem_bytes": int(sh("sysctl", "-n", "hw.memsize")),
            "arch": platform.machine(),
            "power": sh("pmset", "-g", "batt").splitlines()[0]
            if sh("which", "pmset", check=False)
            else "",
        },
        "docker": json.loads(sh("docker", "version", "--format", "{{json .}}")),
        "docker_vm": json.loads(
            sh("docker", "info", "--format", '{"ncpu":{{.NCPU}},"mem_bytes":{{.MemTotal}}}')
        ),
        "compose": sh("docker", "compose", "version", "--short"),
        "k6_version": sh("docker", "compose", "run", "--rm", "-q", "--no-deps", "k6", "version"),
        "api_image_id": api_image,
        "api_python_packages": sh(
            "docker", "run", "--rm", "--entrypoint", "pip", "aead-study-api:local", "freeze"
        ).splitlines(),
        "api_python": sh(
            "docker", "run", "--rm", "--entrypoint", "python", "aead-study-api:local", "-VV"
        ),
        "compose_sha256": sha256_file(ROOT / "compose.yaml"),
        "fixtures_sha256": {
            p.name: sha256_file(p)
            for p in sorted((ROOT / "k6" / "fixtures").glob("payloads-*.json"))
        },
        "aead_kid": json.loads((ROOT / "secrets" / "aead_keys.json").read_text())["kid"],
    }


def probe_ready(port: int, timeout_s: float = 10.0) -> str:
    """GET /healthz over verified TLS 1.3 from the host; return the negotiated version."""
    ctx = ssl.create_default_context(cafile=str(ROOT / "certs" / "ca.pem"))
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            conn = http.client.HTTPSConnection("localhost", port, context=ctx, timeout=2)
            conn.request("GET", "/healthz")
            resp = conn.getresponse()
            resp.read()
            version = conn.sock.version() if conn.sock else ""
            conn.close()
            if resp.status == 200 and version == "TLSv1.3":
                return version
        except OSError:
            pass
        if time.monotonic() > deadline:
            raise RuntimeError(f"API on port {port} not ready after {timeout_s} s")
        time.sleep(0.1)


def recreate_apis() -> dict:
    """Replace both API containers with fresh ones and wait until they serve TLS 1.3."""
    t_begin = utcnow()
    sh(
        "docker",
        "compose",
        "up",
        "-d",
        "--force-recreate",
        "--wait",
        "--wait-timeout",
        "60",
        "api-plain",
        "api-aead",
    )
    t_healthy = utcnow()
    for port in API_HOST_PORT.values():
        probe_ready(port)
    out = {"restart_begin_utc": t_begin, "healthy_utc": t_healthy, "ready_utc": utcnow()}
    for mode, name in API_CONTAINER.items():
        info = json.loads(sh("docker", "inspect", name, "--format", "{{json .}}"))
        out[f"{mode}_container_id"] = info["Id"][:12]
        out[f"{mode}_started_at"] = info["State"]["StartedAt"]
        out[f"{mode}_restart_count"] = info["RestartCount"]
    return out


def run_k6(out_rel: str, run: dict, prof: dict, cfg: dict, resources: Path) -> dict:
    out_dir = ROOT / "results" / "raw" / out_rel
    name = f"aead-study-k6-{run['run_id']}"
    sample_every = int(cfg.get("sampling", {}).get("every", {}).get(run["size"], 0))
    env = {
        "MODE": run["mode"],
        "SIZE": run["size"],
        "VUS": str(run["vus"]),
        "WARMUP_S": str(prof["warmup_s"]),
        "DURATION_S": str(prof["duration_s"]),
        "RUN_ID": run["run_id"],
        "OUT_DIR": f"/results/{out_rel}",
        "SMOKE": "1" if prof.get("smoke") else "0",
        "EXECUTOR": run["executor"],
        "RATE": str(run["rate"] or 0),
        "SAMPLE_EVERY": str(sample_every),
    }
    env_args = list(itertools.chain.from_iterable(("-e", f"{k}={v}") for k, v in env.items()))

    power = power_source()
    if cfg.get("require_ac_power") and power != "AC Power":
        raise RuntimeError(f"host not on AC power ({power or 'unknown'}); aborting before run")
    restart = recreate_apis() if cfg.get("restart_per_run") else {}
    sampler = Sampler(resources, run["run_id"], [API_CONTAINER[run["mode"]]]).start()
    time.sleep(cfg.get("idle_baseline_s", 0))
    started = utcnow()
    sh(
        "docker",
        "compose",
        "run",
        "-d",
        "--no-deps",
        "--name",
        name,
        *env_args,
        "k6",
        "run",
        "-q",
        "/scripts/bench.js",
    )
    sampler.add(name)
    exit_code = int(sh("docker", "wait", name))
    ended = utcnow()
    sampler.stop()
    logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
    log_text, samples = split_k6_log(logs.stdout + logs.stderr)
    (out_dir / f"{run['run_id']}.k6.log").write_text(log_text)
    cap = int(cfg.get("sampling", {}).get("cap", 0))
    truncated = bool(cap) and len(samples) > cap
    if sample_every:
        with (out_dir / f"{run['run_id']}.samples.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(SAMPLE_FIELDS)
            # Thin uniformly (every j-th row) rather than keeping the start of the run.
            w.writerows(samples[:: -(-len(samples) // cap)] if truncated else samples)
    sh("docker", "rm", name)

    timing: dict = {}
    summary_path = out_dir / f"{run['run_id']}.summary.json"
    if summary_path.exists():
        doc = json.loads(summary_path.read_text())
        t0 = doc.get("t0_epoch_ms")
        if t0 is not None:
            ms = lambda x: dt.datetime.fromtimestamp(x / 1000, dt.UTC).isoformat()  # noqa: E731
            timing = {
                "k6_t0_utc": ms(t0),
                "warmup_end_utc": ms(t0 + prof["warmup_s"] * 1000),
                "measure_end_utc": ms(t0 + (prof["warmup_s"] + prof["duration_s"]) * 1000),
            }
    return {
        **run,
        "power_source": power,
        **restart,
        "k6_launch_utc": started,
        **timing,
        "k6_exit_utc": ended,
        "k6_exit_code": exit_code,
        "resource_rows": sampler.rows_written,
        "sample_every": sample_every,
        "samples": min(len(samples), cap) if truncated else len(samples),
        "samples_truncated": truncated,
    }


def build_order(cfg: dict, profile: str, prof: dict) -> list[dict]:
    executor = prof.get("executor", "constant-vus")
    rates = prof.get("rates", {})
    cells = list(itertools.product(prof["modes"], prof["sizes"], prof["vus"]))
    order: list[dict] = []
    for rep in range(prof["reps"]):
        block = cells[:]
        random.Random(f"{cfg['seed']}-{profile}-{rep}").shuffle(block)  # noqa: S311
        for mode, size, vus in block:
            i = len(order)
            rate = rates.get(size) if executor == "constant-arrival-rate" else None
            load = f"a{rate}" if rate else f"c{vus}"
            order.append(
                {
                    "seq": i,
                    "rep": rep,
                    "mode": mode,
                    "size": size,
                    "vus": vus,
                    "executor": executor,
                    "rate": rate,
                    "run_id": f"{i:03d}-{mode}-{size}-{load}-r{rep}",
                }
            )
    return order


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--allow-full", action="store_true", help="required for the full matrix")
    args = ap.parse_args()
    cfg = tomllib.loads((ROOT / "experiment.toml").read_text())
    prof = cfg["profiles"][args.profile]
    if prof.get("gated") and not args.allow_full:
        sys.exit(f"profile {args.profile!r} requires --allow-full (owner approval)")
    if prof.get("executor") == "constant-arrival-rate" and set(prof["sizes"]) - set(prof["rates"]):
        sys.exit("open-loop profile needs a rate for every size")

    if cfg.get("require_ac_power") and power_source() != "AC Power":
        sys.exit(f"host not on AC power ({power_source() or 'unknown'}); connect power first")
    # Hold a no-sleep assertion for this process's lifetime (effective on AC power only).
    caffeinate = subprocess.Popen(["caffeinate", "-s", "-w", str(os.getpid())])  # noqa: S607
    sh("docker", "compose", "up", "-d", "--wait", "api-plain", "api-aead")
    order = build_order(cfg, args.profile, prof)

    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    out_rel = f"{stamp}-{args.profile}"
    out = ROOT / "results" / "raw" / out_rel
    out.mkdir(parents=True)
    (out / "manifest.json").write_text(
        json.dumps(manifest(args.profile, cfg, prof, order), indent=2)
    )
    per_run = (
        prof["warmup_s"]
        + prof["duration_s"]
        + cfg["cooldown_s"]
        + cfg.get("idle_baseline_s", 0)
        + (2 if cfg.get("restart_per_run") else 0)
        + 4
    )
    print(f"{len(order)} runs -> {out.relative_to(ROOT)} (~{len(order) * per_run / 60:.0f} min)")

    sh(
        "docker",
        "compose",
        "run",
        "--rm",
        "-q",
        "--no-deps",
        "-e",
        f"OUT_DIR=/results/{out_rel}",
        "k6",
        "run",
        "-q",
        "/scripts/client_crypto_bench.js",
    )

    rows: list[dict] = []
    for run in order:
        try:
            r = run_k6(out_rel, run, prof, cfg, out / "resources.csv")
        except RuntimeError as e:
            write_runs(out / "runs.csv", rows)
            caffeinate.terminate()
            sys.exit(f"aborted at run {run['run_id']}: {e} ({len(rows)} runs recorded)")
        rows.append(r)
        write_runs(out / "runs.csv", rows)
        log = (out / f"{run['run_id']}.k6.log").read_text().splitlines()
        line = next((x for x in reversed(log) if "measured_reqs=" in x), log[-1] if log else "")
        print(
            f"[{run['seq'] + 1}/{len(order)}] exit={r['k6_exit_code']} "
            f"resource_rows={r['resource_rows']} request_samples={r['samples']} {line}",
            flush=True,
        )
        time.sleep(cfg["cooldown_s"])

    caffeinate.terminate()
    failed = [r["run_id"] for r in rows if r["k6_exit_code"] != 0]
    print(f"done: {len(rows)} runs, {len(failed)} non-zero k6 exits {failed or ''}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
