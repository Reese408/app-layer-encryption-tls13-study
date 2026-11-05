"""Validate the resource sampler against a known load and against `docker stats`.

A throwaway container (the API image, no network) is limited to 1 CPU, allocates
256 MiB, then busy-loops. The sampler should report ~100% of one core and a working
set of at least 256 MiB; a `docker stats --no-stream` reading taken during the same
window is recorded for cross-checking.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sample_resources import Sampler  # noqa: E402

IMAGE = "aead-study-api:local"
NAME = "aead-study-sampler-validation"
PROG = (
    "b = bytearray(256 * 1024 * 1024)\n"
    "for i in range(0, len(b), 4096): b[i] = 1\n"
    "while True: pass\n"
)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--seconds", type=int, default=15)
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    csv_path = args.out / "sampler_validation.csv"
    csv_path.unlink(missing_ok=True)

    subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            NAME,
            "--network",
            "none",
            "--cpus",
            "1",
            "--memory",
            "512m",
            "--entrypoint",
            "python",
            IMAGE,
            "-c",
            PROG,
        ],
        check=True,
        capture_output=True,
    )
    try:
        sampler = Sampler(csv_path, "validation", [NAME]).start()
        time.sleep(args.seconds / 2)
        ds = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}", NAME],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        time.sleep(args.seconds / 2)
        sampler.stop()
    finally:
        subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)

    rows = list(csv.DictReader(csv_path.open()))
    steady = rows[3:]  # skip allocation phase
    first, last = steady[0], steady[-1]
    cpu_pct = (
        (int(last["cpu_total_ns"]) - int(first["cpu_total_ns"]))
        / (int(last["read_ns"]) - int(first["read_ns"]))
        * 100
    )
    mem_mib = max(int(r["mem_working_set_bytes"]) for r in steady) / 2**20
    result = {
        "samples": len(rows),
        "expected": {"cpu_pct_of_one_core": 100.0, "mem_working_set_mib_min": 256.0},
        "measured": {
            "cpu_pct_from_cumulative_counters": round(cpu_pct, 2),
            "cpu_pct_docker_formula_mean": round(
                sum(float(r["cpu_pct_docker"]) for r in steady if r["cpu_pct_docker"])
                / len(steady),
                2,
            ),
            "mem_working_set_mib_max": round(mem_mib, 1),
        },
        "docker_stats_no_stream": json.loads(ds),
        "pass": {
            "cpu_within_10pct": abs(cpu_pct - 100.0) <= 10.0,
            "mem_at_least_256mib": mem_mib >= 256.0,
        },
    }
    (args.out / "sampler_validation.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result["measured"]), json.dumps(result["pass"]))
    print(
        "docker stats:",
        result["docker_stats_no_stream"]["CPUPerc"],
        result["docker_stats_no_stream"]["MemUsage"],
    )
    sys.exit(0 if all(result["pass"].values()) else 1)


if __name__ == "__main__":
    main()
