"""Experiment D (A1): envelope cost breakdown (research-design.md §8). Descriptive only.

Per container process: the median over repetitions of ns per operation. Across the
processes (the unit): median, min and max. Shares are taken per process (component median
divided by whole-operation median), then summarised across processes the same way. The
residual is the whole operation minus the sum of its timed components.

Consistency check: whole seal + open against Experiment A's in-request server timers (`enc`
and `dec`, which time exactly EnvelopeCipher.seal and .open) at 1 VU, from the per-request
sample of the aead runs.

Usage: analysis/envelope_micro.py results/raw/<ts>-envelope-micro [results/raw/<Experiment A>]
  -> results/processed/<ts>-envelope-micro/{breakdown.csv, breakdown.md}
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = {
    "seal": ["seal.urandom", "seal.aad", "seal.aesgcm", "seal.b64", "seal.json"],
    "open": ["open.json", "open.b64", "open.aesgcm"],
}
SIZE_ORDER = ["prompt", "1k", "16k", "256k"]
EXP_A = "20261020T233709Z-full"


def breakdown(t: pd.DataFrame) -> pd.DataFrame:
    med = t.groupby(["proc", "size", "op"]).ns_per_op.median().unstack("op")
    rows = []
    for path, comps in COMPONENTS.items():
        whole = med[f"{path}.whole"]
        parts = {c: med[c] for c in comps}
        parts[f"{path}.residual"] = whole - sum(med[c] for c in comps)
        for name, v in {f"{path}.whole": whole, **parts}.items():
            share = v / whole
            for size in v.index.get_level_values("size").unique():
                x = v.xs(size, level="size") / 1e3  # µs
                sh = share.xs(size, level="size")
                rows.append(
                    {
                        "size": size,
                        "path": path,
                        "op": name,
                        "us_median": x.median(),
                        "us_min": x.min(),
                        "us_max": x.max(),
                        "share_median": sh.median(),
                        "share_min": sh.min(),
                        "share_max": sh.max(),
                        "n_proc": len(x),
                    }
                )
    df = pd.DataFrame(rows)
    df["_s"] = df["size"].map(SIZE_ORDER.index)
    return df.sort_values(["_s", "path", "op"]).drop(columns="_s")


def experiment_a(raw_a: Path) -> pd.DataFrame:
    rows = []
    for size in ("1k", "16k", "256k"):
        parts = [pd.read_csv(p) for p in sorted(raw_a.glob(f"*-aead-{size}-c1-r*.samples.csv"))]
        s = pd.concat(parts)
        rows.append(
            {
                "size": size,
                "a_srv_enc_us_p50": s.srv_enc_ms.median() * 1e3,
                "a_srv_dec_us_p50": s.srv_dec_ms.median() * 1e3,
                "a_runs": len(parts),
                "a_requests": len(s),
            }
        )
    return pd.DataFrame(rows)


def to_md(raw: Path, b: pd.DataFrame, t: pd.DataFrame, a: pd.DataFrame | None) -> str:
    sizes = (
        t.groupby("size")[["plaintext_bytes", "envelope_bytes"]]
        .first()
        .reindex([s for s in SIZE_ORDER if s in t["size"].unique()])
    )
    lines = [
        f"# Envelope cost breakdown (A1): `{raw.name}`",
        "",
        "Descriptive microbenchmark in the API image (2 CPUs, no network). Per process: the median",
        "over repetitions; reported: median [min, max] across processes. A share is a fraction",
        "of the whole operation. Residual = whole − Σ components.",
        "",
        "| size | plaintext B | envelope B | envelope / plaintext |",
        "|---|---|---|---|",
    ]
    for s, r in sizes.iterrows():
        ratio = r.envelope_bytes / r.plaintext_bytes
        lines.append(f"| {s} | {r.plaintext_bytes:.0f} | {r.envelope_bytes:.0f} | {ratio:.3f} |")
    lines += [
        "",
        "| size | path | operation | µs per op | share of whole |",
        "|---|---|---|---|---|",
    ]
    for r in b.itertuples():
        lines.append(
            f"| {r.size} | {r.path} | {r.op} | {r.us_median:.3f} [{r.us_min:.3f}, {r.us_max:.3f}]"
            f" | {r.share_median:.3f} [{r.share_min:.3f}, {r.share_max:.3f}] |"
        )
    if a is not None:
        w = b[b.op.isin(["seal.whole", "open.whole"])].pivot(
            index="size", columns="op", values="us_median"
        )
        m = a.set_index("size").join(w, how="inner")
        lines += [
            "",
            "## Consistency with Experiment A (in-request server timers, aead, 1 VU)",
            "",
            "| size | micro seal µs | A `enc` p50 µs | micro open µs | A `dec` p50 µs | A requests |",
            "|---|---|---|---|---|---|",
        ]
        for s, r in m.iterrows():
            lines.append(
                f"| {s} | {r['seal.whole']:.2f} | {r.a_srv_enc_us_p50:.2f} | "
                f"{r['open.whole']:.2f} | {r.a_srv_dec_us_p50:.2f} | {r.a_requests:.0f} |"
            )
    return "\n".join(lines) + "\n"


def main() -> None:
    raw = Path(sys.argv[1]).resolve()
    raw_a = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else ROOT / "results" / "raw" / EXP_A
    t = pd.read_csv(raw / "timings.csv")
    b = breakdown(t)
    a = experiment_a(raw_a) if raw_a.exists() else None
    out = ROOT / "results" / "processed" / raw.name
    out.mkdir(parents=True, exist_ok=True)
    b.to_csv(out / "breakdown.csv", index=False)
    if a is not None:
        a.to_csv(out / "experiment_a_server_timers.csv", index=False)
    md = to_md(raw, b, t, a)
    (out / "breakdown.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
