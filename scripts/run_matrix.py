"""Run a benchmark profile from experiment.toml and write raw results.

Output: results/raw/<UTC timestamp>-<profile>/
  manifest.json            environment, versions, config, fixture hashes, run order
  runs.csv                 one row per run (ids, timing, k6 exit code, sampler row count)
  <run_id>.summary.json    k6 end-of-test summary (exact percentiles over measured requests)
  <run_id>.k6.log          k6 stdout/stderr
  resources.csv            ~1 Hz Docker stats for the API under test and the k6 container
  client_crypto_bench.json client-side WebCrypto cost per operation (once per session)
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import itertools
import json
import platform
import random
import subprocess
import sys
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from sample_resources import Sampler  # noqa: E402

API_CONTAINER = {"plain": "aead-study-api-plain-1", "aead": "aead-study-api-aead-1"}


def sh(*cmd: str, check: bool = True) -> str:
    return subprocess.run(cmd, cwd=ROOT, check=check, capture_output=True, text=True).stdout.strip()


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def manifest(profile: str, cfg: dict, prof: dict, order: list[dict]) -> dict:
    api_image = sh("docker", "image", "inspect", "aead-study-api:local", "--format", "{{.Id}}")
    return {
        "profile": profile,
        "started_utc": dt.datetime.now(dt.UTC).isoformat(),
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


def run_k6(out_rel: str, run: dict, prof: dict, resources: Path) -> dict:
    name = f"aead-study-k6-{run['run_id']}"
    env = {
        "MODE": run["mode"],
        "SIZE": run["size"],
        "VUS": str(run["vus"]),
        "WARMUP_S": str(prof["warmup_s"]),
        "DURATION_S": str(prof["duration_s"]),
        "RUN_ID": run["run_id"],
        "OUT_DIR": f"/results/{out_rel}",
        "SMOKE": "1" if prof.get("smoke") else "0",
    }
    env_args = list(itertools.chain.from_iterable(("-e", f"{k}={v}") for k, v in env.items()))
    started = dt.datetime.now(dt.UTC).isoformat()
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
    sampler = Sampler(resources, run["run_id"], [API_CONTAINER[run["mode"]], name]).start()
    exit_code = int(sh("docker", "wait", name))
    sampler.stop()
    logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
    (ROOT / "results" / "raw" / out_rel / f"{run['run_id']}.k6.log").write_text(
        logs.stdout + logs.stderr
    )
    sh("docker", "rm", name)
    return {
        **run,
        "started_utc": started,
        "ended_utc": dt.datetime.now(dt.UTC).isoformat(),
        "k6_exit_code": exit_code,
        "resource_rows": sampler.rows_written,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--allow-full", action="store_true", help="required for the full matrix")
    args = ap.parse_args()
    cfg = tomllib.loads((ROOT / "experiment.toml").read_text())
    prof = cfg["profiles"][args.profile]
    if args.profile == "full" and not args.allow_full:
        sys.exit("full matrix requires --allow-full (run only after pilot review)")

    sh("docker", "compose", "up", "-d", "--wait", "api-plain", "api-aead")

    cells = list(itertools.product(prof["modes"], prof["sizes"], prof["vus"]))
    order: list[dict] = []
    for rep in range(prof["reps"]):
        block = cells[:]
        random.Random(f"{cfg['seed']}-{args.profile}-{rep}").shuffle(block)  # noqa: S311
        for mode, size, vus in block:
            i = len(order)
            order.append(
                {
                    "seq": i,
                    "rep": rep,
                    "mode": mode,
                    "size": size,
                    "vus": vus,
                    "run_id": f"{i:03d}-{mode}-{size}-c{vus}-r{rep}",
                }
            )

    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    out_rel = f"{stamp}-{args.profile}"
    out = ROOT / "results" / "raw" / out_rel
    out.mkdir(parents=True)
    (out / "manifest.json").write_text(
        json.dumps(manifest(args.profile, cfg, prof, order), indent=2)
    )
    per_run = prof["warmup_s"] + prof["duration_s"] + cfg["cooldown_s"] + 4
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

    rows = []
    for run in order:
        r = run_k6(out_rel, run, prof, out / "resources.csv")
        rows.append(r)
        summary = (out / f"{run['run_id']}.k6.log").read_text().strip().splitlines()
        print(
            f"[{run['seq'] + 1}/{len(order)}] exit={r['k6_exit_code']} "
            f"samples={r['resource_rows']} {summary[-1] if summary else ''}",
            flush=True,
        )
        time.sleep(cfg["cooldown_s"])

    with (out / "runs.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    failed = [r["run_id"] for r in rows if r["k6_exit_code"] != 0]
    print(f"done: {len(rows)} runs, {len(failed)} non-zero k6 exits {failed or ''}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
