"""Experiment D (A1): breakdown of the server-side envelope cost (research-design.md §8).

Host mode (default): runs the worker in 5 separate containers of the API image
(--cpus 2, --network none; only scripts/ and k6/fixtures/ mounted, read-only) and writes
results/raw/<ts>-envelope-micro/{manifest.json, timings.csv}.

Worker mode (--worker, inside the container): times the whole seal/open operations of
app.crypto_envelope.EnvelopeCipher and their components on the study's fixture plaintexts,
and prints one JSON line per (size, operation) with the per-repetition times.

Keys are generated in-process for each worker; nothing secret is read, written or printed.
"""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROCESSES = 5
REPEATS = 25
MIN_REPEAT_NS = 20_000_000  # 20 ms
SEED = 20261021
SIZES = ("1k", "16k", "256k", "prompt")
PATH = "/v1/process"


def plaintexts(root: Path) -> dict[str, list[bytes]]:
    out = {}
    for s in ("1k", "16k", "256k"):
        items = json.loads((root / "k6" / "fixtures" / f"payloads-{s}.json").read_text())["items"]
        out[s] = [it["req"].encode() for it in items]
    prompts = json.loads((root / "k6" / "fixtures" / "prompts.json").read_text())["items"]
    out["prompt"] = [
        json.dumps({"id": p["id"], "max_tokens": 32, "prompt": p["prompt"]}).encode()
        for p in prompts
    ]
    return out


def worker(root: Path, proc: int) -> None:
    # Use the API image's own copy of the application code when present (exactly what the
    # API runs); fall back to the repository for local testing.
    sys.path.insert(0, "/srv" if Path("/srv/app").exists() else str(root))
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    from app.crypto_envelope import EnvelopeCipher, KeySet, build_aad

    keys = KeySet.generate("kmicro")
    server = EnvelopeCipher(keys, "server")
    client = EnvelopeCipher(keys, "client")
    enc_aead, dec_aead = AESGCM(keys.s2c), AESGCM(keys.c2s)
    rng = random.Random(f"{SEED}-{proc}")  # noqa: S311 - operation order only

    def bench(size: str, pts: list[bytes]) -> None:
        # Prepared once, outside timing: what each operation consumes.
        envs = [client.seal(p, "POST", PATH) for p in pts]  # requests the server opens
        docs = [json.loads(e) for e in envs]
        cts = [base64.b64decode(d["ct"]) for d in docs]
        nonces = [base64.b64decode(d["n"]) for d in docs]
        n_b64 = [d["n"] for d in docs]
        ct_b64 = [d["ct"] for d in docs]
        aad_c2s = build_aad("c2s", "POST", PATH, keys.kid)
        aad_s2c = build_aad("s2c", "POST", PATH, keys.kid)
        seal_ct = [enc_aead.encrypt(nonces[i], pts[i], aad_s2c) for i in range(len(pts))]
        k = len(pts)

        ops = {
            "seal.whole": lambda i: server.seal(pts[i], "POST", PATH),
            "seal.urandom": lambda i: os.urandom(12),
            "seal.aad": lambda i: build_aad("s2c", "POST", PATH, keys.kid),
            "seal.aesgcm": lambda i: enc_aead.encrypt(nonces[i], pts[i], aad_s2c),
            "seal.b64": lambda i: (
                base64.b64encode(nonces[i]).decode(),
                base64.b64encode(seal_ct[i]).decode(),
            ),
            "seal.json": lambda i: json.dumps(
                {"v": 1, "kid": keys.kid, "n": n_b64[i], "ct": ct_b64[i]}, separators=(",", ":")
            ).encode(),
            "open.whole": lambda i: server.open(envs[i], "POST", PATH),
            "open.json": lambda i: json.loads(envs[i]),
            "open.b64": lambda i: (
                base64.b64decode(n_b64[i], validate=True),
                base64.b64decode(ct_b64[i], validate=True),
            ),
            "open.aesgcm": lambda i: dec_aead.decrypt(nonces[i], cts[i], aad_c2s),
        }
        # Correctness: the component path reproduces the whole operation's result.
        assert all(server.open(envs[i], "POST", PATH) == pts[i] for i in range(k))
        assert all(dec_aead.decrypt(nonces[i], cts[i], aad_c2s) == pts[i] for i in range(k))

        def run(fn, n: int) -> int:
            t0 = time.perf_counter_ns()
            for j in range(n):
                fn(j % k)
            return time.perf_counter_ns() - t0

        loops = {}
        for name, fn in ops.items():
            n = 1
            while run(fn, n) < MIN_REPEAT_NS:
                n *= 2
            loops[name] = n
        times: dict[str, list[float]] = {name: [] for name in ops}
        for _ in range(REPEATS):
            order = list(ops)
            rng.shuffle(order)
            for name in order:
                times[name].append(run(ops[name], loops[name]) / loops[name])
        for name in ops:
            print(
                json.dumps(
                    {
                        "proc": proc,
                        "size": size,
                        "plaintext_bytes": sum(map(len, pts)) / k,
                        "envelope_bytes": sum(map(len, envs)) / k,
                        "op": name,
                        "loops": loops[name],
                        "ns_per_op": times[name],
                    }
                ),
                flush=True,
            )

    for size, pts in plaintexts(root).items():
        bench(size, pts)


def host() -> None:
    sh = lambda *c: subprocess.run(c, cwd=ROOT, check=True, capture_output=True, text=True).stdout  # noqa: E731
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
    out = ROOT / "results" / "raw" / f"{stamp}-envelope-micro"
    out.mkdir(parents=True)
    rows = []
    for proc in range(PROCESSES):
        t0 = dt.datetime.now(dt.UTC).isoformat()
        text = sh(
            "docker", "run", "--rm", "--cpus", "2", "--network", "none",
            "-v", f"{ROOT / 'scripts'}:/repo/scripts:ro",
            "-v", f"{ROOT / 'k6' / 'fixtures'}:/repo/k6/fixtures:ro",
            "--entrypoint", "python", "aead-study-api:local",
            "/repo/scripts/envelope_micro.py", "--worker", "--proc", str(proc), "--root", "/repo",
        )  # fmt: skip
        for line in text.splitlines():
            d = json.loads(line)
            for rep, ns in enumerate(d.pop("ns_per_op")):
                rows.append({**d, "rep": rep, "ns_per_op": round(ns, 2), "proc_start_utc": t0})
        print(f"process {proc + 1}/{PROCESSES} done", flush=True)
    with (out / "timings.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    image = sh("docker", "image", "inspect", "aead-study-api:local", "--format", "{{.Id}}").strip()
    pkgs = sh("docker", "run", "--rm", "--entrypoint", "pip", "aead-study-api:local", "freeze")
    openssl = sh(
        "docker", "run", "--rm", "--entrypoint", "python", "aead-study-api:local", "-c",
        "from cryptography.hazmat.backends.openssl.backend import backend as b;"
        "print(b.openssl_version_text())",
    ).strip()  # fmt: skip
    (out / "manifest.json").write_text(
        json.dumps(
            {
                "profile": "envelope-micro",
                "started_utc": rows[0]["proc_start_utc"],
                "git": {
                    "commit": sh("git", "rev-parse", "HEAD").strip(),
                    "dirty": bool(sh("git", "status", "--porcelain", "--untracked-files=no")),
                },
                "protocol": "docs/research-design.md §8",
                "processes": PROCESSES,
                "repeats": REPEATS,
                "min_repeat_ns": MIN_REPEAT_NS,
                "seed": SEED,
                "container": {"cpus": 2, "network": "none", "image_id": image},
                "openssl": openssl,
                "api_python_packages": pkgs.splitlines(),
                "host_power": sh("pmset", "-g", "batt").splitlines()[0],
            },
            indent=2,
        )
    )
    print(f"{len(rows)} rows -> {out.relative_to(ROOT)}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worker", action="store_true")
    ap.add_argument("--proc", type=int, default=0)
    ap.add_argument("--root", default=str(ROOT))
    a = ap.parse_args()
    if a.worker:
        worker(Path(a.root), a.proc)
    else:
        host()


if __name__ == "__main__":
    main()
