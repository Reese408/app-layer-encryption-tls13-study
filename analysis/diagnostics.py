"""Descriptive diagnostics cited in the reports (not hypothesis tests).

  isolation   per-cell range (max - min across repetitions) of peak API working set, and the
              Spearman correlation between run order and peak working set
  outside     from the per-request SAMPLE: median http_req_waiting minus server app time, per
              mode and cell; this is time spent outside the request handler (network, TLS
              record processing, HTTP parsing, event-loop scheduling)
  server      median server compute per request (decrypt + workload + encrypt) per mode and cell
  wire        bytes sent and received per measured request per mode and size

Usage: analysis/diagnostics.py results/raw/<dir> [...]  -> stdout (Markdown)
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pandas as pd
from scipy.stats import spearmanr

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aggregate import ROOT, SIZE_ORDER, M, per_run  # noqa: E402


def md(df: pd.DataFrame, digits: int = 4) -> str:
    """Markdown table without optional dependencies."""

    def f(v: object) -> str:
        return f"{v:.{digits}f}" if isinstance(v, float) else str(v)

    rows = [list(df.columns), ["---"] * len(df.columns)]
    rows += [[f(v) for v in r] for r in df.itertuples(index=False)]
    return "\n".join("| " + " | ".join(map(str, r)) + " |" for r in rows)


def _order(df: pd.DataFrame) -> pd.DataFrame:
    return df.assign(_s=df["size"].map(SIZE_ORDER)).sort_values(["_s", "load"]).drop(columns="_s")


def samples(raw: Path, runs: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for r in runs.itertuples():
        p = raw / f"{r.run_id}.samples.csv"
        if p.exists():
            s = pd.read_csv(p)
            parts.append(s.assign(mode=r.mode, size=r.size, load=r.load, run_id=r.run_id))
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()


def main() -> None:
    cfg = tomllib.loads((ROOT / "experiment.toml").read_text())
    for arg in sys.argv[1:]:
        raw = Path(arg).resolve()
        runs = per_run(raw, cfg)
        print(f"# Diagnostics: `{raw.name}`\n")

        g = runs.groupby(["mode", "size", "load"]).api_mem_mib_max
        rng = g.max() - g.min()
        rho = spearmanr(runs.seq, runs.api_mem_mib_max).statistic
        print("## isolation\n")
        print(f"- peak working-set range across repetitions per cell: max {rng.max():.1f} MiB, ")
        print(f"  median {rng.median():.1f} MiB")
        print(f"- Spearman rho(run order, peak working set) = {rho:.2f}\n")

        s = samples(raw, runs)
        if s.empty:
            print("(no per-request sample in this dataset)\n")
            continue
        s["outside_ms"] = s.waiting_ms - s.srv_app_ms
        s["compute_ms"] = s.srv_dec_ms.fillna(0) + s.srv_work_ms + s.srv_enc_ms.fillna(0)
        tab = (
            s.groupby(["size", "load", "mode"])[
                ["req_ms", "waiting_ms", "srv_app_ms", "outside_ms", "compute_ms"]
            ]
            .median()
            .unstack("mode")
        )
        tab.columns = [f"{a} {b}" for a, b in tab.columns]
        print("## outside / server (medians of the per-request sample, ms)\n")
        print(md(_order(tab.reset_index())))
        print()

        wire = []
        for r in runs.itertuples():
            m = json.loads((raw / f"{r.run_id}.summary.json").read_text())["k6"]["metrics"]
            n = m[f"http_reqs{M}"]["values"]["count"]
            wire.append(
                {
                    "mode": r.mode,
                    "size": r.size,
                    "sent_B_per_req": m[f"data_sent{M}"]["values"]["count"] / n,
                    "recv_B_per_req": m[f"data_received{M}"]["values"]["count"] / n,
                }
            )
        w = pd.DataFrame(wire).groupby(["size", "mode"]).median().round(0).reset_index()
        print("## wire (median bytes per measured request)\n")
        print(
            w.assign(_s=w["size"].map(SIZE_ORDER)).sort_values("_s").drop(columns="_s").pipe(md, 0)
        )
        print()


if __name__ == "__main__":
    main()
