"""Aggregate one raw results directory into per-run, per-cell, and comparison tables.

Reads ONLY results/raw/<dir>/ and writes results/processed/<dir>/:
  runs.csv         one row per k6 run (latency, throughput, errors, stage timings, CPU, memory,
                   open-loop rate/drops, per-request sample checks)
  cells.csv        per (mode, size, load): median across repetitions + rep-to-rep spread
  comparison.csv   aead vs plain per (size, load)
  comparison.md    the same, as Markdown tables

`load` is `c<VUs>` for the closed-loop (constant-vus) executor and `a<rate>` for the
open-loop (constant-arrival-rate) executor. The two are never pooled.

CPU utilisation is computed from cumulative counters over the k6 measurement window
[t0 + warmup, t0 + warmup + duration], where t0 is recorded by k6 setup() on the same
(Docker VM) clock as the sampler. Values are % of ONE core.
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
M = "{phase:measure}"
SIZE_ORDER = {"1k": 0, "16k": 1, "256k": 2}


def _v(metrics: dict, name: str, stat: str) -> float:
    return metrics.get(f"{name}{M}", {}).get("values", {}).get(stat, float("nan"))


def _raw(metrics: dict, name: str, stat: str, default: float = float("nan")) -> float:
    return metrics.get(name, {}).get("values", {}).get(stat, default)


def sample_stats(raw: Path, run_id: str) -> dict:
    """Summary of the deterministic per-request sample, if the run recorded one."""
    p = raw / f"{run_id}.samples.csv"
    if not p.exists():
        return {}
    s = pd.read_csv(p)
    if s.empty:
        return {"sample_n": 0}
    return {
        "sample_n": len(s),
        "sample_ok_frac": ((s.status == 200) & (s.integrity == 1)).mean(),
        "sample_req_p50_ms": s.req_ms.quantile(0.50),
        "sample_req_p95_ms": s.req_ms.quantile(0.95),
        "sample_req_p99_ms": s.req_ms.quantile(0.99),
        "sample_iter_p50_ms": s.iter_ms.quantile(0.50),
        "sample_client_enc_p50_ms": s.client_enc_ms.quantile(0.50),
        "sample_client_dec_p50_ms": s.client_dec_ms.quantile(0.50),
        "sample_t_min_ms": s.t_ms.min(),
        "sample_t_max_ms": s.t_ms.max(),
    }


def idle_baseline_mib(res: pd.DataFrame, container: str, run_id: str, t0_ns: int) -> float:
    """Working set of the API container in the samples taken before k6 started."""
    r = res[(res.container == container) & (res.run_id == run_id) & (res.read_ns < t0_ns)]
    return r.mem_working_set_bytes.max() / 2**20 if len(r) else float("nan")


def cpu_window(
    res: pd.DataFrame, container: str, start_ns: int, end_ns: int
) -> tuple[float, float]:
    r = res[(res.container == container) & (res.read_ns >= start_ns) & (res.read_ns <= end_ns)]
    if len(r) < 2:
        return float("nan"), float("nan")
    r = r.sort_values("read_ns")
    a, b = r.iloc[0], r.iloc[-1]
    cpu = (b.cpu_total_ns - a.cpu_total_ns) / (b.read_ns - a.read_ns) * 100
    return cpu, r.mem_working_set_bytes.max() / 2**20


def per_run(raw: Path, cfg: dict) -> pd.DataFrame:
    runs = pd.read_csv(raw / "runs.csv")
    res = pd.read_csv(raw / "resources.csv")
    rows = []
    for _, run in runs.iterrows():
        doc = json.loads((raw / f"{run.run_id}.summary.json").read_text())
        m = doc["k6"]["metrics"]
        dur = doc["duration_s"]
        start = int((doc["t0_epoch_ms"] + doc["warmup_s"] * 1000) * 1e6)
        end = start + int(dur * 1e9)
        api = "aead-study-api-aead-1" if run["mode"] == "aead" else "aead-study-api-plain-1"
        api_cpu, api_mem = cpu_window(res, api, start, end)
        k6_cpu, k6_mem = cpu_window(res, f"aead-study-k6-{run.run_id}", start, end)
        n = _v(m, "http_reqs", "count")
        executor = doc.get("executor") or "constant-vus"
        rate = doc.get("rate")
        rows.append(
            {
                "run_id": run.run_id,
                "seq": run.seq,
                "rep": run.rep,
                "mode": run["mode"],
                "size": run["size"],
                "vus": run.vus,
                "executor": executor,
                "target_rate": rate if rate else float("nan"),
                "load": f"a{rate}" if rate else f"c{run.vus}",
                "load_order": rate if rate else run.vus,
                "k6_exit_code": run.k6_exit_code,
                "requests": n,
                "rps": n / dur,
                # dropped_iterations is not phase-tagged by k6: whole run incl. warm-up.
                "dropped_iterations": _raw(m, "dropped_iterations", "count", 0.0),
                "api_idle_mem_mib": idle_baseline_mib(
                    res, api, run.run_id, int(doc["t0_epoch_ms"] * 1e6)
                ),
                "lat_avg_ms": _v(m, "http_req_duration", "avg"),
                "lat_p50_ms": _v(m, "http_req_duration", "med"),
                "lat_p95_ms": _v(m, "http_req_duration", "p(95)"),
                "lat_p99_ms": _v(m, "http_req_duration", "p(99)"),
                "lat_max_ms": _v(m, "http_req_duration", "max"),
                "e2e_avg_ms": _v(m, "iteration_duration", "avg"),
                "e2e_p50_ms": _v(m, "iteration_duration", "med"),
                "e2e_p95_ms": _v(m, "iteration_duration", "p(95)"),
                "e2e_p99_ms": _v(m, "iteration_duration", "p(99)"),
                "client_overhead_avg_ms": _v(m, "iteration_duration", "avg")
                - _v(m, "http_req_duration", "avg"),
                "client_enc_avg_ms": _v(m, "client_encrypt_ms", "avg"),
                "client_dec_avg_ms": _v(m, "client_decrypt_ms", "avg"),
                "srv_dec_avg_ms": _v(m, "server_dec_ms", "avg"),
                "srv_work_avg_ms": _v(m, "server_work_ms", "avg"),
                "srv_enc_avg_ms": _v(m, "server_enc_ms", "avg"),
                "srv_app_avg_ms": _v(m, "server_app_ms", "avg"),
                "srv_app_p99_ms": _v(m, "server_app_ms", "p(99)"),
                "error_rate": _v(m, "http_req_failed", "rate"),
                "checks_rate": _v(m, "checks", "rate"),
                "integrity_rate": _v(m, "integrity_ok", "rate"),
                "api_cpu_pct": api_cpu,
                "api_mem_mib_max": api_mem,
                "k6_cpu_pct": k6_cpu,
                "k6_mem_mib_max": k6_mem,
                **sample_stats(raw, run.run_id),
            }
        )
    df = pd.DataFrame(rows)
    df["achieved_rate_frac"] = df.rps / df.target_rate
    # CPU-milliseconds the API process spends per completed request (from measured CPU and rps).
    df["api_cpu_ms_per_req"] = df.api_cpu_pct / 100 * 1000 / df.rps
    df["client_share_of_e2e"] = df.client_overhead_avg_ms / df.e2e_avg_ms
    df["k6_cpu_frac_of_limit"] = df.k6_cpu_pct / (cfg["client_cpu_limit"] * 100)
    df["client_saturated"] = df.k6_cpu_frac_of_limit > cfg["client_saturation_threshold"]
    # A run counts as client-OK only if k6 CPU was measured AND is below the threshold.
    df["client_ok"] = df.k6_cpu_frac_of_limit <= cfg["client_saturation_threshold"]
    # Single Uvicorn worker: the event loop is one thread, so ~100% of one core = saturated.
    df["server_saturated"] = df.api_cpu_pct > 90
    return df


def _sort(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    return df.sort_values(cols, key=lambda s: s.map(SIZE_ORDER) if s.name == "size" else s)


def per_cell(df: pd.DataFrame) -> pd.DataFrame:
    keys = ["mode", "size", "load"]
    num = df.drop(
        columns=["run_id", "seq", "rep", "client_saturated", "server_saturated", "executor"]
    ).groupby(keys)
    cells = num.median(numeric_only=True)
    spread = df.groupby(keys)[["lat_p50_ms", "lat_p99_ms", "rps"]].agg(
        lambda s: (s.max() - s.min()) / s.median() * 100
    )
    spread.columns = [f"{c}_range_pct" for c in spread.columns]
    flags = df.groupby(keys)[["client_saturated", "server_saturated"]].any()
    reps = df.groupby(keys).size().rename("reps")
    out = cells.join(spread).join(flags).join(reps).reset_index()
    return _sort(out, ["size", "load_order", "mode"])


def compare(cells: pd.DataFrame) -> pd.DataFrame:
    p = cells[cells["mode"] == "plain"].set_index(["size", "load"])
    a = cells[cells["mode"] == "aead"].set_index(["size", "load"])
    c = pd.DataFrame(index=p.index)
    c["load_order"] = p.load_order
    for k in ["lat_p50_ms", "lat_p95_ms", "lat_p99_ms", "e2e_p50_ms", "e2e_p99_ms"]:
        c[f"plain_{k}"], c[f"aead_{k}"] = p[k], a[k]
        c[f"delta_{k}"] = a[k] - p[k]
        c[f"delta_{k}_pct"] = (a[k] / p[k] - 1) * 100
    c["plain_rps"], c["aead_rps"] = p.rps, a.rps
    c["delta_rps_pct"] = (a.rps / p.rps - 1) * 100
    c["aead_srv_crypto_ms"] = a.srv_dec_avg_ms + a.srv_enc_avg_ms
    # Denominator excludes `app`, which includes awaiting the request body and therefore
    # absorbs event-loop queueing when the server is saturated.
    c["aead_srv_crypto_share_of_compute_pct"] = (
        c.aead_srv_crypto_ms / (c.aead_srv_crypto_ms + a.srv_work_avg_ms) * 100
    )
    c["plain_api_cpu_ms_per_req"] = p.api_cpu_ms_per_req
    c["aead_api_cpu_ms_per_req"] = a.api_cpu_ms_per_req
    c["delta_api_cpu_ms_per_req"] = a.api_cpu_ms_per_req - p.api_cpu_ms_per_req
    c["aead_client_share_of_e2e_pct"] = a.client_share_of_e2e * 100
    c["plain_client_overhead_ms"], c["aead_client_overhead_ms"] = (
        p.client_overhead_avg_ms,
        a.client_overhead_avg_ms,
    )
    c["plain_api_cpu_pct"], c["aead_api_cpu_pct"] = p.api_cpu_pct, a.api_cpu_pct
    c["max_k6_cpu_frac"] = pd.concat([p.k6_cpu_frac_of_limit, a.k6_cpu_frac_of_limit], axis=1).max(
        axis=1
    )
    c["any_client_saturated"] = p.client_saturated | a.client_saturated
    c["any_server_saturated"] = p.server_saturated | a.server_saturated
    c = c.reset_index()
    return _sort(c, ["size", "load_order"])


def to_markdown(cmp: pd.DataFrame, cells: pd.DataFrame, raw: Path) -> str:
    f = lambda x, d=3: "n/a" if pd.isna(x) else f"{x:.{d}f}"  # noqa: E731
    lines = [
        f"# Aggregated results: `{raw.name}`",
        "",
        "Medians across repetitions. Latency = k6 `http_req_duration` (ms); "
        "E2E = k6 `iteration_duration` (ms, includes client crypto). CPU = % of one core. "
        "Load `cN` = closed loop with N VUs; `aR` = open loop at R requests/s.",
        "",
        "## Latency and throughput (aead vs plain)",
        "",
        "| size | load | p50 plain | p50 aead | Δp50 | p95 plain | p95 aead | p99 plain | p99 aead | Δp99 | rps plain | rps aead | Δrps |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in cmp.itertuples():
        lines.append(
            f"| {r.size} | {r.load} | {f(r.plain_lat_p50_ms)} | {f(r.aead_lat_p50_ms)} | "
            f"{f(r.delta_lat_p50_ms)} ({f(r.delta_lat_p50_ms_pct, 1)}%) | {f(r.plain_lat_p95_ms)} | "
            f"{f(r.aead_lat_p95_ms)} | {f(r.plain_lat_p99_ms)} | {f(r.aead_lat_p99_ms)} | "
            f"{f(r.delta_lat_p99_ms)} ({f(r.delta_lat_p99_ms_pct, 1)}%) | {f(r.plain_rps, 0)} | "
            f"{f(r.aead_rps, 0)} | {f(r.delta_rps_pct, 1)}% |"
        )
    lines += [
        "",
        "## Where the time goes (aead, means in ms)",
        "",
        "| size | load | srv decrypt | srv work | srv encrypt | srv crypto share of compute | API CPU-ms/req plain | API CPU-ms/req aead | Δ CPU-ms/req | client overhead plain | client overhead aead | client share of E2E (aead) | client enc (Date.now) | client dec (Date.now) | E2E p50 plain | E2E p50 aead |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    a = cells[cells["mode"] == "aead"].set_index(["size", "load"])
    for r in cmp.itertuples():
        x = a.loc[(r.size, r.load)]
        lines.append(
            f"| {r.size} | {r.load} | {f(x.srv_dec_avg_ms, 4)} | {f(x.srv_work_avg_ms, 4)} | "
            f"{f(x.srv_enc_avg_ms, 4)} | {f(r.aead_srv_crypto_share_of_compute_pct, 1)}% | "
            f"{f(r.plain_api_cpu_ms_per_req, 4)} | {f(r.aead_api_cpu_ms_per_req, 4)} | "
            f"{f(r.delta_api_cpu_ms_per_req, 4)} | "
            f"{f(r.plain_client_overhead_ms)} | {f(r.aead_client_overhead_ms)} | "
            f"{f(r.aead_client_share_of_e2e_pct, 1)}% | "
            f"{f(x.client_enc_avg_ms)} | {f(x.client_dec_avg_ms)} | {f(r.plain_e2e_p50_ms)} | {f(r.aead_e2e_p50_ms)} |"
        )
    lines += [
        "",
        "## Resources, errors, and bottleneck flags",
        "",
        "| mode | size | load | API CPU % | API mem MiB | k6 CPU % | k6 % of limit | errors | integrity | client sat. | server sat. | p50 range % | p99 range % | rps range % |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in cells.itertuples():
        lines.append(
            f"| {r.mode} | {r.size} | {r.load} | {f(r.api_cpu_pct, 1)} | {f(r.api_mem_mib_max, 1)} | "
            f"{f(r.k6_cpu_pct, 1)} | {f(r.k6_cpu_frac_of_limit * 100, 1)}% | {f(r.error_rate, 4)} | "
            f"{f(r.integrity_rate, 4)} | {r.client_saturated} | {r.server_saturated} | "
            f"{f(r.lat_p50_ms_range_pct, 1)} | {f(r.lat_p99_ms_range_pct, 1)} | {f(r.rps_range_pct, 1)} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    raw_root = ROOT / "results" / "raw"
    raw = (
        Path(sys.argv[1]).resolve()
        if len(sys.argv) > 1 and sys.argv[1]
        else max((p for p in raw_root.iterdir() if (p / "runs.csv").exists()), key=lambda p: p.name)
    )
    cfg = tomllib.loads((ROOT / "experiment.toml").read_text())
    out = ROOT / "results" / "processed" / raw.name
    out.mkdir(parents=True, exist_ok=True)
    runs = per_run(raw, cfg)
    cells = per_cell(runs)
    cmp = compare(cells)
    runs.to_csv(out / "runs.csv", index=False)
    cells.to_csv(out / "cells.csv", index=False)
    cmp.to_csv(out / "comparison.csv", index=False)
    md = to_markdown(cmp, cells, raw)
    (out / "comparison.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
