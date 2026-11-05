"""Export recorded results as OpenMetrics, for post-hoc viewing in Prometheus and Grafana.

The benchmark itself never talks to Prometheus or Grafana: a live metrics pipeline would add
containers and client-side work during the measured runs and so change what is measured.
Instead, this script converts files that already exist under results/raw/ (and the derived
tables under results/processed/) into OpenMetrics text with the ORIGINAL timestamps. promtool
turns that text into Prometheus TSDB blocks ("backfilling"), and Grafana reads them.

Series (all gauges; labels: dataset, experiment, run_id, mode, size, load, rep):
  bench_container_cpu_cores        CPU used by the API container / k6 container, from
                                   consecutive cumulative-counter readings (role=api|k6)
  bench_container_working_set_bytes  memory working set (role=api|k6)
  bench_run_phase                  0 idle baseline, 1 warm-up, 2 measurement (per sampler read)
  bench_run_value                  one point per run at its measurement end, metric=<column of
                                   analysis/aggregate.per_run> (exact k6 percentiles, rps, ...)
  bench_sampled_request_ms         the deterministic 1-in-K request SAMPLE (not the population),
                                   summarised per 1 s window: stat=p50|p95|max|n,
                                   component=req|waiting|srv_app|client_enc|client_dec|tls
  bench_sampled_request_bucket     per-window cumulative histogram of sampled req_ms (le, ms)
  bench_effect                     paired aead/plain effect per cell from stats.csv,
                                   stat=ratio|ci95_lo|ci95_hi|simci_lo|simci_hi|p_t|p_holm|...
  bench_validation_pass            1/0 per run and acceptance criterion (validation.json)

Usage: analysis/export_prometheus.py results/raw/<dir> [...]
  -> results/processed/grafana/openmetrics.txt and dashboard JSON with the datasets' time range
"""

from __future__ import annotations

import datetime as dt
import json
import math
import sys
import tomllib
from collections import defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aggregate import ROOT, SIZE_ORDER, per_run  # noqa: E402

OUT = ROOT / "results" / "processed" / "grafana"
RUN_VALUES = [
    "lat_p50_ms",
    "lat_p95_ms",
    "lat_p99_ms",
    "lat_max_ms",
    "e2e_p50_ms",
    "e2e_p99_ms",
    "rps",
    "requests",
    "api_cpu_pct",
    "api_cpu_ms_per_req",
    "api_mem_mib_max",
    "api_idle_mem_mib",
    "k6_cpu_frac_of_limit",
    "srv_dec_avg_ms",
    "srv_work_avg_ms",
    "srv_enc_avg_ms",
    "srv_app_avg_ms",
    "client_enc_avg_ms",
    "client_dec_avg_ms",
    "error_rate",
    "integrity_rate",
    "dropped_iterations",
    "dropped_measure",
    "achieved_rate_frac",
]
SAMPLE_COMPONENTS = {
    "req": "req_ms",
    "waiting": "waiting_ms",
    "srv_app": "srv_app_ms",
    "client_enc": "client_enc_ms",
    "client_dec": "client_dec_ms",
    "tls": "tls_handshake_ms",
}
LE_MS = [0.05 * 2**i for i in range(15)] + [math.inf]  # 0.05 ms .. 819 ms, log2
EFFECT_STATS = {
    "ratio": "ratio",
    "ci95_lo": "ratio_ci95_lo",
    "ci95_hi": "ratio_ci95_hi",
    "simci_lo": "ratio_simci_lo",
    "simci_hi": "ratio_simci_hi",
    "pct_change": "pct_change",
    "delta": "delta",
    "p_t": "p_t",
    "p_holm": "p_holm",
    "cliffs_delta": "cliffs_delta",
    "n_blocks": "n_blocks",
}


def ts(s: str) -> float:
    """RFC 3339 (ns or µs precision) -> float seconds since the epoch."""
    s = s.replace("Z", "+00:00")
    if "." in s:
        head, rest = s.split(".", 1)
        frac, tz = rest[:-6], rest[-6:]
        s = f"{head}.{frac[:6]}{tz}"
    return dt.datetime.fromisoformat(s).timestamp()


def cell(size: str, load_order: float) -> str:
    """Sortable cell key: payload size, then concurrency or rate (used for display order)."""
    return f"{SIZE_ORDER[size]}-{int(load_order):06d}"


def esc(v: object) -> str:
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


class Writer:
    """Collect samples per family and series; emit them in OpenMetrics order."""

    def __init__(self) -> None:
        self.data: dict[str, dict[tuple, list[tuple[float, float]]]] = defaultdict(
            lambda: defaultdict(list)
        )

    def add(self, name: str, labels: dict, t: float, v: float) -> None:
        if v is None or (isinstance(v, float) and not math.isfinite(v)):
            return
        self.data[name][tuple(sorted(labels.items()))].append((t, float(v)))

    def render(self) -> str:
        out: list[str] = []
        for name in sorted(self.data):
            out.append(f"# TYPE {name} gauge")
            for key in sorted(self.data[name]):
                lab = ",".join(f'{k}="{esc(v)}"' for k, v in key)
                last = None
                for t, v in sorted(self.data[name][key]):
                    tr = round(t, 3)
                    if tr == last:  # timestamps must be strictly increasing within a series
                        continue
                    last = tr
                    out.append(f"{name}{{{lab}}} {v:.9g} {tr:.3f}")
        out.append("# EOF")
        return "\n".join(out) + "\n"


def export(raw: Path, cfg: dict, w: Writer) -> tuple[float, float]:
    manifest = json.loads((raw / "manifest.json").read_text())
    experiment = manifest.get("profile", raw.name.split("-", 1)[-1])
    runs = pd.read_csv(raw / "runs.csv")
    res = pd.read_csv(raw / "resources.csv")
    pr = per_run(raw, cfg).set_index("run_id")
    base = {"dataset": raw.name, "experiment": experiment}
    t_lo, t_hi = math.inf, -math.inf

    for _, r in runs.iterrows():
        lab = {
            **base,
            "run_id": r.run_id,
            "mode": r["mode"],
            "size": r["size"],
            "load": pr.loc[r.run_id, "load"],
            "rep": str(r.rep),
            "cell": cell(r["size"], pr.loc[r.run_id, "load_order"]),
        }
        warm, meas, end = ts(r.k6_t0_utc), ts(r.warmup_end_utc), ts(r.measure_end_utc)
        t_lo, t_hi = min(t_lo, ts(r.k6_launch_utc) - 5), max(t_hi, ts(r.k6_exit_utc) + 5)

        for role, container in (
            ("api", f"aead-study-api-{r['mode']}-1"),
            ("k6", f"aead-study-k6-{r.run_id}"),
        ):
            s = res[(res.run_id == r.run_id) & (res.container == container)].sort_values("read_ns")
            prev = None
            for row in s.itertuples():
                t = row.read_ns / 1e9
                w.add(
                    "bench_container_working_set_bytes",
                    {**lab, "role": role},
                    t,
                    row.mem_working_set_bytes,
                )
                if prev is not None and row.read_ns > prev.read_ns:
                    cores = (row.cpu_total_ns - prev.cpu_total_ns) / (row.read_ns - prev.read_ns)
                    w.add("bench_container_cpu_cores", {**lab, "role": role}, t, cores)
                if role == "api":
                    phase = 0 if t < warm else (1 if t < meas else (2 if t <= end else 0))
                    w.add("bench_run_phase", lab, t, phase)
                prev = row

        for col in RUN_VALUES:
            if col in pr.columns:
                w.add("bench_run_value", {**lab, "metric": col}, end, pr.loc[r.run_id, col])

        sp = raw / f"{r.run_id}.samples.csv"
        if sp.exists():
            smp = pd.read_csv(sp)
            t0 = ts(r.k6_t0_utc)
            # Summarised per 1 s window of the run (one point per window, at its end).
            win = (smp.t_ms // 1000).astype(int)
            for comp, col in SAMPLE_COMPONENTS.items():
                if col not in smp or smp[col].isna().all():
                    continue
                g = smp[col].groupby(win)
                for stat, ser in (
                    ("p50", g.median()),
                    ("p95", g.quantile(0.95)),
                    ("max", g.max()),
                    ("n", g.count()),
                ):
                    for sec, v in ser.items():
                        w.add(
                            "bench_sampled_request_ms",
                            {**lab, "component": comp, "stat": stat},
                            t0 + sec + 1,
                            v,
                        )
            # Cumulative histogram per window (sampled requests <= le), for heatmaps.
            for sec, v in smp.req_ms.groupby(win):
                for le in LE_MS:
                    w.add(
                        "bench_sampled_request_bucket",
                        {**lab, "le": "+Inf" if le == math.inf else f"{le:g}"},
                        t0 + sec + 1,
                        int((v <= le).sum()),
                    )

    proc = ROOT / "results" / "processed" / raw.name
    if (proc / "stats.csv").exists():
        st = pd.read_csv(proc / "stats.csv")
        for _, e in st.iterrows():
            lab = {
                **base,
                "metric": e.metric,
                "family": e.family,
                "size": e["size"],
                "load": e["load"],
                "cell": cell(e["size"], e["load_order"]),
            }
            for stat, col in EFFECT_STATS.items():
                w.add("bench_effect", {**lab, "stat": stat}, t_hi, e[col])
    if (proc / "validation.json").exists():
        val = json.loads((proc / "validation.json").read_text())
        for r in val["runs"]:
            for k, v in r.items():
                if isinstance(v, bool) and k != "pass":
                    w.add(
                        "bench_validation_pass",
                        {**base, "run_id": r["run_id"], "criterion": k},
                        t_hi,
                        int(v),
                    )
    return t_lo, t_hi


def main() -> None:
    dirs = [Path(a).resolve() for a in sys.argv[1:]]
    if not dirs:
        sys.exit(__doc__)
    cfg = tomllib.loads((ROOT / "experiment.toml").read_text())
    w = Writer()
    lo, hi = math.inf, -math.inf
    for d in dirs:
        a, b = export(d, cfg, w)
        lo, hi = min(lo, a), max(hi, b)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "openmetrics.txt").write_text(w.render())
    # Dashboards are provisioned from templates with the exported time range filled in.
    dash_out = OUT / "dashboards"
    dash_out.mkdir(exist_ok=True)
    rng = {
        "from": dt.datetime.fromtimestamp(lo, dt.UTC).isoformat().replace("+00:00", "Z"),
        "to": dt.datetime.fromtimestamp(hi, dt.UTC).isoformat().replace("+00:00", "Z"),
    }
    for tpl in sorted((ROOT / "observability" / "dashboards").glob("*.json")):
        d = json.loads(tpl.read_text())
        d["time"] = rng
        (dash_out / tpl.name).write_text(json.dumps(d, indent=1))
    n = sum(len(v) for fam in w.data.values() for v in fam.values())
    print(
        f"{n} samples, {sum(len(f) for f in w.data.values())} series -> {OUT / 'openmetrics.txt'}"
    )
    print(f"time range {rng['from']} .. {rng['to']}")


if __name__ == "__main__":
    main()
