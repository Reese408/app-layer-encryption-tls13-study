"""Experiment C (H5, LLM inference): determinism check and interleaved benchmark runs.

Protocol: docs/research-design.md §7 (pre-registered 2026-10-21).

  run_infer.py --determinism            -> results/raw/<ts>-infer-determinism/
      Each prompt x length is generated `determinism_repeats` times directly against Ollama
      (cycling through the prompts, so no prompt repeats back to back). generations.csv
      records counts, durations and a SHA-256 of the text; the text itself is not stored.

  run_infer.py --profile infer-pilot    -> results/raw/<ts>-infer-pilot/
  run_infer.py --profile infer --allow-full
      Per run: recreate both API containers (R1), check Ollama (version, digest, residency),
      start the samplers (Docker stats for both APIs and k6; host ps/ioreg for Ollama and the
      GPU), record an idle baseline, run k6/infer.js, check Ollama again.

Output per benchmark session (in addition to the files of run_matrix.py):
  <run_id>.samples.csv   every measured request (complete log, not a sample)
  host_resources.csv     Ollama processes (RSS, CPU time) and GPU counters, host clock
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time
import tomllib
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))
from run_matrix import (  # noqa: E402
    API_CONTAINER,
    manifest,
    power_source,
    recreate_apis,
    sh,
    sha256_file,
    utcnow,
    write_runs,
)
from sample_resources import HostSampler, Sampler  # noqa: E402

from app.inference import OPTIONS, Ollama  # noqa: E402

# Column order of the `I,` lines written by k6/infer.js.
INFER_FIELDS = [
    "iter",
    "t_ms",
    "mode",
    "prompt_id",
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
    "srv_infer_ms",
    "srv_enc_ms",
    "srv_app_ms",
    "done_reason",
    "prompt_eval_count",
    "prompt_eval_ms",
    "eval_count",
    "eval_ms",
    "load_ms",
    "ollama_total_ms",
    "req_body_bytes",
    "resp_body_bytes",
    "resp_sha256_16",
    "iter_ms",
]
_LINE_RE = re.compile(r'msg="I,([^"]*)"')
PROMPTS = ROOT / "k6" / "fixtures" / "prompts.json"


def get_json(url: str, timeout: float = 5.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 - fixed local URL
        return json.loads(r.read())


def ollama_state(inf: dict) -> dict:
    """Version, model digest and residency (from /api/ps) of the host Ollama server."""
    base = inf["ollama_host_url"]
    tags = get_json(f"{base}/api/tags")["models"]
    tag = next((m for m in tags if m["name"] == inf["model"]), {})
    ps = next((m for m in get_json(f"{base}/api/ps")["models"] if m["name"] == inf["model"]), {})
    return {
        "version": get_json(f"{base}/api/version")["version"],
        "digest": tag.get("digest", ""),
        "loaded": bool(ps),
        "size": ps.get("size", 0),
        "size_vram": ps.get("size_vram", 0),
        "context_length": ps.get("context_length", ""),
        "expires_at": ps.get("expires_at", ""),
    }


def check_state(st: dict, inf: dict, need_loaded: bool) -> None:
    if st["version"] != inf["ollama_version"]:
        raise RuntimeError(f"Ollama version {st['version']} != pinned {inf['ollama_version']}")
    if st["digest"] != inf["model_digest"]:
        raise RuntimeError(f"model digest {st['digest'][:12]} != pinned")
    if need_loaded and not (st["loaded"] and st["size"] and st["size_vram"] == st["size"]):
        raise RuntimeError(f"model not fully resident on the GPU: {st}")


def load_model(inf: dict) -> dict:
    """Load the model with the experiment's options (same num_ctx, so no later reload)."""
    return Ollama(inf["ollama_host_url"], inf["model"]).generate("Reply with OK.", 1)["meta"]


def prompts() -> list[dict]:
    return json.loads(PROMPTS.read_text())["items"]


def infer_manifest(profile: str, cfg: dict, prof: dict, order: list[dict], st: dict) -> dict:
    m = manifest(profile, cfg, prof, order)
    m["inference"] = {
        "ollama_state_at_start": st,
        "ollama_show": get_json_post(
            f"{cfg['inference']['ollama_host_url']}/api/show", {"model": cfg["inference"]["model"]}
        ),
        "options": OPTIONS,
        "request_fields": {"stream": False, "think": False, "keep_alive": -1},
        "prompts_sha256": sha256_file(PROMPTS),
        "prompt_bytes": [len(p["prompt"].encode()) for p in prompts()],
        "ollama_app_version": sh(
            "defaults",
            "read",
            "/Applications/Ollama.app/Contents/Info.plist",
            "CFBundleShortVersionString",
            check=False,
        ),
    }
    return m


def get_json_post(url: str, body: dict) -> dict:
    req = urllib.request.Request(  # noqa: S310 - fixed local URL
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310
        doc = json.loads(r.read())
    # Keep metadata only: drop the (large) tensor list and the licence text.
    return {k: v for k, v in doc.items() if k not in ("tensors", "license", "modelfile")}


# --- determinism check ----------------------------------------------------------------------


def determinism(cfg: dict) -> None:
    inf = cfg["inference"]
    st = ollama_state(inf)
    check_state(st, inf, need_loaded=False)
    load_model(inf)
    check_state(ollama_state(inf), inf, need_loaded=True)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "results" / "raw" / f"{stamp}-infer-determinism"
    out.mkdir(parents=True)
    client = Ollama(inf["ollama_host_url"], inf["model"])
    rows = []
    for rep in range(inf["determinism_repeats"]):
        for length, n in inf["lengths"].items():
            for p in prompts():
                t0 = time.perf_counter_ns()
                r = client.generate(p["prompt"], n)
                wall = (time.perf_counter_ns() - t0) / 1e6
                m = r["meta"]
                rows.append(
                    {
                        "rep": rep,
                        "length": length,
                        "num_predict": n,
                        "prompt_id": p["id"],
                        "utc": utcnow(),
                        "done_reason": m["done_reason"],
                        "prompt_eval_count": m["prompt_eval_count"],
                        "eval_count": m["eval_count"],
                        "prompt_eval_ms": m["prompt_eval_duration"] / 1e6,
                        "eval_ms": m["eval_duration"] / 1e6,
                        "load_ms": m["load_duration"] / 1e6,
                        "total_ms": m["total_duration"] / 1e6,
                        "wall_ms": round(wall, 3),
                        "resp_sha256": hashlib.sha256(r["response"].encode()).hexdigest(),
                        "resp_bytes": len(r["response"].encode()),
                    }
                )
                print(
                    f"rep={rep} {length} {p['id']} eval={m['eval_count']} "
                    f"{m['done_reason']} sha={rows[-1]['resp_sha256'][:12]}",
                    flush=True,
                )
    with (out / "generations.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    end = ollama_state(inf)
    (out / "manifest.json").write_text(
        json.dumps(
            {
                "profile": "infer-determinism",
                "started_utc": rows[0]["utc"],
                "git": {
                    "commit": sh("git", "rev-parse", "HEAD"),
                    "dirty": bool(sh("git", "status", "--porcelain", "--untracked-files=no")),
                },
                "inference": {**inf, "options": OPTIONS, "prompts_sha256": sha256_file(PROMPTS)},
                "ollama_state_at_start": st,
                "ollama_state_at_end": end,
                "power": power_source(),
            },
            indent=2,
        )
    )
    print(f"{len(rows)} generations -> {out.relative_to(ROOT)}")


# --- benchmark runs -------------------------------------------------------------------------


def build_order(cfg: dict, profile: str, prof: dict) -> list[dict]:
    """Blocks of one run per length (seeded order); the first mode alternates across blocks
    from a seeded starting point per length."""
    lengths = prof["lengths"]
    rng = random.Random(f"{cfg['seed']}-{profile}-first")  # noqa: S311
    offset = {L: rng.randrange(2) for L in lengths}
    order: list[dict] = []
    for rep in range(prof["reps"]):
        block = lengths[:]
        random.Random(f"{cfg['seed']}-{profile}-{rep}").shuffle(block)  # noqa: S311
        for L in block:
            first = ("plain", "aead")[(rep + offset[L]) % 2]
            i = len(order)
            order.append(
                {
                    "seq": i,
                    "rep": rep,
                    "length": L,
                    "max_tokens": cfg["inference"]["lengths"][L],
                    "first": first,
                    "run_id": f"{i:03d}-infer-{L}-{first}first-r{rep}",
                }
            )
    return order


def split_log(text: str) -> tuple[str, list[list[str]]]:
    other, rows = [], []
    for line in text.splitlines():
        m = _LINE_RE.search(line)
        if m:
            fields = m.group(1).split(",")
            if len(fields) != len(INFER_FIELDS):
                raise ValueError(
                    f"request line has {len(fields)} fields, expected {len(INFER_FIELDS)}"
                )
            rows.append(fields)
        else:
            other.append(line)
    return "\n".join(other) + ("\n" if other else ""), rows


def run_one(out_rel: str, run: dict, prof: dict, cfg: dict) -> dict:
    inf = cfg["inference"]
    out_dir = ROOT / "results" / "raw" / out_rel
    name = f"aead-study-k6-{run['run_id']}"
    env = {
        "FIRST": run["first"],
        "MAX_TOKENS": str(run["max_tokens"]),
        "WARMUP_N": str(inf["warmup_n"]),
        "MEASURE_N": str(prof["measure_n"]),
        "RUN_ID": run["run_id"],
        "OUT_DIR": f"/results/{out_rel}",
    }
    env_args = [a for k, v in env.items() for a in ("-e", f"{k}={v}")]

    power = power_source()
    if cfg.get("require_ac_power") and power != "AC Power":
        raise RuntimeError(f"host not on AC power ({power or 'unknown'}); aborting before run")
    restart = recreate_apis() if cfg.get("restart_per_run") else {}
    pre = ollama_state(inf)
    check_state(pre, inf, need_loaded=True)
    sampler = Sampler(out_dir / "resources.csv", run["run_id"], list(API_CONTAINER.values()))
    sampler.start()
    host = HostSampler(out_dir / "host_resources.csv", run["run_id"], prof["gpu_sampling"])
    host.start()
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
        "/scripts/infer.js",
    )
    sampler.add(name)
    exit_code = int(sh("docker", "wait", name))
    ended = utcnow()
    sampler.stop()
    host.stop()
    post = ollama_state(inf)
    logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
    log_text, reqs = split_log(logs.stdout + logs.stderr)
    (out_dir / f"{run['run_id']}.k6.log").write_text(log_text)
    with (out_dir / f"{run['run_id']}.samples.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(INFER_FIELDS)
        w.writerows(reqs)
    sh("docker", "rm", name)

    timing: dict = {}
    summary = out_dir / f"{run['run_id']}.summary.json"
    if summary.exists():
        t0 = json.loads(summary.read_text()).get("t0_epoch_ms")
        if t0 is not None:
            timing["k6_t0_utc"] = dt.datetime.fromtimestamp(t0 / 1000, dt.UTC).isoformat()
    return {
        **run,
        "power_source": power,
        **restart,
        **{f"ollama_pre_{k}": v for k, v in pre.items()},
        "k6_launch_utc": started,
        **timing,
        "k6_exit_utc": ended,
        "k6_exit_code": exit_code,
        **{f"ollama_post_{k}": v for k, v in post.items()},
        "resource_rows": sampler.rows_written,
        "host_rows": host.rows_written,
        "requests_logged": len(reqs),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--profile")
    g.add_argument("--determinism", action="store_true")
    ap.add_argument("--allow-full", action="store_true", help="required for gated profiles")
    args = ap.parse_args()
    cfg = tomllib.loads((ROOT / "experiment.toml").read_text())
    if not PROMPTS.exists():
        sys.exit("k6/fixtures/prompts.json missing: run scripts/gen_prompts.py")
    if cfg.get("require_ac_power") and power_source() != "AC Power":
        sys.exit(f"host not on AC power ({power_source() or 'unknown'}); connect power first")
    caffeinate = subprocess.Popen(["caffeinate", "-s", "-w", str(os.getpid())])  # noqa: S607
    try:
        if args.determinism:
            determinism(cfg)
        else:
            bench(cfg, args.profile, args.allow_full)
    finally:
        caffeinate.terminate()


def bench(cfg: dict, profile: str, allow_full: bool) -> None:
    prof = cfg["profiles"][profile]
    if prof.get("executor") != "infer":
        sys.exit(f"profile {profile!r} is not an inference profile")
    if prof.get("gated") and not allow_full:
        sys.exit(f"profile {profile!r} requires --allow-full (owner approval)")
    if prof["measure_n"] % 8 or cfg["inference"]["warmup_n"] % 8:
        sys.exit("warmup_n and measure_n must be multiples of the 8-request cycle")
    inf = cfg["inference"]
    st = ollama_state(inf)
    check_state(st, inf, need_loaded=False)
    load_model(inf)
    check_state(ollama_state(inf), inf, need_loaded=True)
    sh("docker", "compose", "up", "-d", "--wait", "api-plain", "api-aead")
    order = build_order(cfg, profile, prof)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    out_rel = f"{stamp}-{profile}"
    out = ROOT / "results" / "raw" / out_rel
    out.mkdir(parents=True)
    (out / "manifest.json").write_text(
        json.dumps(infer_manifest(profile, cfg, prof, order, st), indent=2)
    )
    print(f"{len(order)} runs -> {out.relative_to(ROOT)}", flush=True)
    rows: list[dict] = []
    for run in order:
        try:
            r = run_one(out_rel, run, prof, cfg)
        except RuntimeError as e:
            write_runs(out / "runs.csv", rows)
            sys.exit(f"aborted at run {run['run_id']}: {e} ({len(rows)} runs recorded)")
        rows.append(r)
        write_runs(out / "runs.csv", rows)
        log = (out / f"{run['run_id']}.k6.log").read_text().splitlines()
        line = next((x for x in reversed(log) if "e2e_p50=" in x), log[-1] if log else "")
        print(f"[{run['seq'] + 1}/{len(order)}] exit={r['k6_exit_code']} {line}", flush=True)
        time.sleep(cfg["cooldown_s"])
    failed = [r["run_id"] for r in rows if r["k6_exit_code"] != 0]
    print(f"done: {len(rows)} runs, {len(failed)} non-zero k6 exits {failed or ''}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
