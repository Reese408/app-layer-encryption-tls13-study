"""Check a raw results directory against the acceptance criteria for the revised pipeline.

The criteria below were fixed before the validation runs were executed. Each is
evaluated per run, and the script exits non-zero if any run fails any criterion.

  run health   k6 exit 0; error rate 0; integrity 1; warm-up requests > 0
  R1 restart   (a) both API containers have a new container ID in every run
               (b) Docker restart count 0
               (c) API under test started after the previous run's measurement window ended
                   and before this run's k6 t0 (both on the Docker VM clock)
               (d) readiness probe completed before k6 was launched (host clock)
               (e) an idle working-set baseline exists and is within ±15% of the
                   per-mode median baseline
  R6 sample    (a) sample file present and not truncated
               (b) |samples - measured requests / K| <= max(2, 1% of expected)
               (c) every sampled request: status 200, integrity 1, measurement phase,
                   index divisible by K, indices unique
               (d) sample p50 within 5% and sample p95 within 10% of k6's exact values
  R3 open loop (a) dropped_iterations == 0 (whole run, including warm-up)
               (b) achieved request rate within ±1% of the target
               (informative) schedule lag of sampled iterations, p99 relative to minimum
  client       k6 CPU measured and below the saturation threshold
               (`client_saturation_threshold`); missing k6 CPU samples fail
  host         (added 2026-10-20 after a host sleep interrupted the first validation attempt,
               before any data from the repeat was examined) host on AC power at run start, and
               k6 wall time (launch -> exit) <= warm-up + duration + 20 s, which detects a
               suspended host or VM

Usage: analysis/validate_pipeline.py results/raw/<dir>
  -> results/processed/<dir>/validation.{json,md}
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
import tomllib
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aggregate import ROOT, M, per_run  # noqa: E402

_TS = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:\d\d)$")


def ts_ns(s: str) -> int:
    """RFC 3339 timestamp (any sub-second precision) -> integer ns since the epoch."""
    base, frac, tz = _TS.match(s).groups()
    secs = dt.datetime.fromisoformat(base + ("+00:00" if tz == "Z" else tz)).timestamp()
    return int(secs) * 1_000_000_000 + int(((frac or "") + "0" * 9)[:9])


def main() -> None:
    raw = Path(sys.argv[1]).resolve()
    cfg = tomllib.loads((ROOT / "experiment.toml").read_text())
    runs_meta = pd.read_csv(raw / "runs.csv")
    df = per_run(raw, cfg).set_index("run_id")
    manifest = json.loads((raw / "manifest.json").read_text())
    # Use the configuration recorded for this dataset, not the current experiment.toml.
    restart = bool(manifest["config"].get("restart_per_run"))
    idle_median = df.groupby("mode").api_idle_mem_mib.median()

    results = []
    prev = None
    seen_ids: set[str] = set()
    for _, meta in runs_meta.sort_values("seq").iterrows():
        rid = meta.run_id
        r = df.loc[rid]
        doc = json.loads((raw / f"{rid}.summary.json").read_text())
        m = doc["k6"]["metrics"]
        total_reqs = m.get("http_reqs", {}).get("values", {}).get("count", 0)
        measured = m.get(f"http_reqs{M}", {}).get("values", {}).get("count", 0)
        c: dict[str, object] = {}

        c["health"] = bool(
            r.k6_exit_code == 0
            and r.error_rate == 0
            and r.integrity_rate == 1
            and r.checks_rate == 1
            and total_reqs - measured > 0
        )

        if restart and "plain_container_id" not in runs_meta:
            c["r1_fields_present"] = False  # restart was configured but not recorded
        elif restart:
            ids = {meta.plain_container_id, meta.aead_container_id}
            c["r1a_new_containers"] = not (ids & seen_ids)
            seen_ids |= ids
            c["r1b_restart_count_0"] = (
                int(meta.plain_restart_count) == 0 and int(meta.aead_restart_count) == 0
            )
            started = ts_ns(meta[f"{meta['mode']}_started_at"])
            t0 = int(doc["t0_epoch_ms"] * 1e6)
            after_prev = prev is None or started > ts_ns(prev.measure_end_utc)
            c["r1c_start_order"] = bool(after_prev and started < t0)
            c["r1d_ready_before_k6"] = ts_ns(meta.ready_utc) <= ts_ns(meta.k6_launch_utc)
            base = r.api_idle_mem_mib
            med = idle_median[meta["mode"]]
            c["r1e_idle_baseline"] = bool(not np.isnan(base) and abs(base - med) <= 0.15 * med)
            c["idle_mem_mib"] = round(float(base), 2)

        k = int(meta.get("sample_every", 0) or 0)
        if k:
            sp = raw / f"{rid}.samples.csv"
            s = pd.read_csv(sp) if sp.exists() else pd.DataFrame()
            expected = measured / k
            c["r6a_present_not_truncated"] = bool(
                sp.exists() and not bool(meta.samples_truncated) and len(s) > 0
            )
            c["r6b_count"] = bool(abs(len(s) - expected) <= max(2, 0.01 * expected))
            if len(s):
                c["r6c_valid_rows"] = bool(
                    (s.status == 200).all()
                    and (s.integrity == 1).all()
                    and (s.t_ms >= doc["warmup_s"] * 1000).all()
                    and (s.iter % k == 0).all()
                    and s.iter.is_unique
                )
                p50 = m[f"http_req_duration{M}"]["values"]["med"]
                p95 = m[f"http_req_duration{M}"]["values"]["p(95)"]
                e50 = s.req_ms.quantile(0.5) / p50 - 1
                e95 = s.req_ms.quantile(0.95) / p95 - 1
                c["r6d_quantiles"] = bool(abs(e50) <= 0.05 and abs(e95) <= 0.10)
                c["sample_n"], c["sample_expected"] = len(s), round(expected, 1)
                c["sample_p50_err_pct"] = round(e50 * 100, 2)
                c["sample_p95_err_pct"] = round(e95 * 100, 2)

        if r.executor == "constant-arrival-rate":
            c["r3a_no_drops"] = bool(r.dropped_iterations == 0)
            c["r3b_rate"] = bool(abs(r.achieved_rate_frac - 1) <= 0.01)
            c["achieved_rate_frac"] = round(float(r.achieved_rate_frac), 5)
            if k and len(s):
                lag = s.t_ms - s.iter * 1000.0 / r.target_rate
                c["sched_lag_p99_ms"] = round(float((lag - lag.min()).quantile(0.99)), 3)

        c["host_ac_power"] = str(meta.get("power_source", "")) == "AC Power"
        wall_s = (ts_ns(meta.k6_exit_utc) - ts_ns(meta.k6_launch_utc)) / 1e9
        c["host_no_suspension"] = bool(wall_s <= doc["warmup_s"] + doc["duration_s"] + 20)
        c["k6_wall_s"] = round(wall_s, 2)
        # Unknown k6 CPU (no resource samples) fails: absence of evidence is not a pass.
        c["client_not_saturated"] = bool(r.client_ok)
        c["k6_cpu_frac_of_limit"] = round(float(r.k6_cpu_frac_of_limit), 4)
        c["api_cpu_pct"] = round(float(r.api_cpu_pct), 1)
        c["api_mem_mib_max"] = round(float(r.api_mem_mib_max), 1)
        checks = {key: v for key, v in c.items() if isinstance(v, bool)}
        results.append({"run_id": rid, "mode": meta["mode"], "pass": all(checks.values()), **c})
        prev = meta

    res = pd.DataFrame(results)
    bool_cols = [
        col
        for col in res.columns
        if col not in ("pass", "mode", "run_id")
        and res[col].dropna().map(lambda v: isinstance(v, bool | np.bool_)).all()
        and res[col].notna().any()
    ]
    summary = {
        "raw": raw.name,
        "runs": len(res),
        "runs_passing": int(res["pass"].sum()),
        "criteria": {
            col: f"{int(res[col].dropna().astype(bool).sum())}/{int(res[col].notna().sum())}"
            for col in bool_cols
        },
        "k6_cpu_frac_of_limit_max": float(res.k6_cpu_frac_of_limit.max()),
    }
    if "idle_mem_mib" in res:
        g = res.groupby("mode").idle_mem_mib
        summary["idle_mem_mib_by_mode"] = {
            mode: [float(lo), float(hi)]
            for mode, lo, hi in zip(g.min().index, g.min(), g.max(), strict=True)
        }
    if "sample_p50_err_pct" in res:
        summary["sample_p50_err_pct_absmax"] = float(res.sample_p50_err_pct.abs().max())
        summary["sample_p95_err_pct_absmax"] = float(res.sample_p95_err_pct.abs().max())
    if "sched_lag_p99_ms" in res:
        summary["sched_lag_p99_ms_max"] = float(res.sched_lag_p99_ms.max())
    if "achieved_rate_frac" in res:
        summary["achieved_rate_frac_range"] = [
            float(res.achieved_rate_frac.min()),
            float(res.achieved_rate_frac.max()),
        ]

    out = ROOT / "results" / "processed" / raw.name
    out.mkdir(parents=True, exist_ok=True)
    (out / "validation.json").write_text(
        json.dumps({"summary": summary, "runs": results}, indent=2, default=str)
    )
    md = [f"# Pipeline validation: `{raw.name}`", "", "```json", json.dumps(summary, indent=2)]
    md += ["```", "", "```", res.to_string(index=False), "```"]
    (out / "validation.md").write_text("\n".join(md) + "\n")
    print(json.dumps(summary, indent=2))
    failed = res[~res["pass"]]
    if len(failed):
        print("FAILED RUNS:")
        print(failed.T.to_string())
    sys.exit(0 if failed.empty else 1)


if __name__ == "__main__":
    main()
