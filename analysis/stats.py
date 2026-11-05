"""Run-level inference for aead vs plain (pre-specified before the full matrix; see
docs/research-design.md §5).

Unit of analysis: one benchmark run. Requests inside a run are serially dependent, so they
are never treated as independent observations. Each repetition `rep` is a randomized block
containing every (mode, size, load) cell once, so aead and plain runs of the same cell and
block are paired; pairing removes slow drift between blocks.

Per cell and metric, with n = number of complete blocks:
  ratio       geometric mean of aead/plain over blocks, i.e. exp(mean(ln aead - ln plain)),
              with a 95% CI and a Bonferroni-adjusted simultaneous CI (paired t on log ratio)
  delta       mean of per-block differences aead - plain, in metric units, with a 95% CI
  p_t         two-sided paired t-test on the log ratio (df = n - 1)
  p_holm      Holm-Bonferroni adjusted p within the family (one family = one metric across
              all cells of one experiment)
  robustness  blocks in which aead > plain; exact two-sided sign-flip permutation p on the
              log ratio (minimum attainable 2 / 2^n); exact Mann-Whitney U p (unpaired,
              minimum attainable 2 / C(2n, n)); Cliff's delta (unpaired, range -1..1)

H4 (Δ grows with payload size): for each load level and block, the OLS slope of Δp50 (ms)
against log2(payload bytes); the block slopes are averaged, with a t-based 95% CI and
one-sample t-test; Holm across load levels.

Eligibility: only runs with k6 exit 0 and measured client CPU below the saturation
threshold enter inference; exclusions are counted per cell. Family size for Holm is the
planned number of cells, so lost or untestable cells make the correction stricter, never
looser.

Usage: analysis/stats.py results/raw/<dir>   -> results/processed/<dir>/stats.{csv,md}
"""

from __future__ import annotations

import itertools
import sys
import tomllib
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aggregate import ROOT, SIZE_ORDER, per_run  # noqa: E402

ALPHA = 0.05
SIZE_BYTES = {"1k": 1024, "16k": 16 * 1024, "256k": 256 * 1024}

# (metric column, family label, role). Throughput is excluded for the open-loop experiment
# because the arrival rate is fixed by design there.
METRICS = [
    ("lat_p50_ms", "H1 median latency", "primary"),
    ("lat_p99_ms", "H2 p99 latency", "primary"),
    ("rps", "H3 throughput", "primary"),
    ("lat_p95_ms", "H2 p95 latency", "secondary"),
    ("e2e_p50_ms", "E2E median (incl. client crypto)", "secondary"),
    ("api_cpu_ms_per_req", "RQ3 API CPU-ms per request", "secondary"),
    ("api_mem_mib_max", "RQ3 API peak working set", "secondary"),
]


def holm(p: list[float], m: int | None = None) -> list[float]:
    """Holm-Bonferroni step-down adjusted p-values.

    `m` is the planned family size. Hypotheses that are missing (cells without a complete
    pair) or untestable (NaN p, e.g. zero-variance log ratios) still count towards m, which
    is equivalent to giving them p = 1; the correction never becomes less strict because
    data were lost. NaN inputs return NaN.
    """
    idx = [i for i, x in enumerate(p) if not np.isnan(x)]
    m = len(p) if m is None else m
    if m < len(p):
        raise ValueError("planned family size smaller than the number of hypotheses")
    out = [float("nan")] * len(p)
    running = 0.0
    for rank, i in enumerate(sorted(idx, key=lambda i: p[i])):
        running = max(running, min(1.0, (m - rank) * p[i]))
        out[i] = running
    return out


def sign_flip_p(x: np.ndarray) -> float:
    """Exact two-sided paired permutation p-value for mean(x) = 0 (all 2^n sign flips)."""
    n = len(x)
    obs = abs(x.mean())
    flips = np.array(list(itertools.product([1, -1], repeat=n)))
    null = np.abs((flips * x).mean(axis=1))
    return float((null >= obs - 1e-12).mean())


def cliffs_delta(a: np.ndarray, b: np.ndarray) -> float:
    """P(a > b) - P(a < b) over all pairs."""
    diff = np.sign(a[:, None] - b[None, :])
    return float(diff.mean())


def t_ci(x: np.ndarray, level: float) -> tuple[float, float]:
    n = len(x)
    if n < 2:
        return float("nan"), float("nan")
    se = x.std(ddof=1) / np.sqrt(n)
    if se == 0:
        return float(x.mean()), float(x.mean())
    h = stats.t.ppf(1 - (1 - level) / 2, n - 1) * se
    return float(x.mean() - h), float(x.mean() + h)


def threshold_test(log_r: np.ndarray, threshold_ratio: float, level: float = 0.95) -> dict:
    """One-sided bounds on a geometric-mean ratio against a threshold (H5, §7.5).

    H0: ratio >= threshold; H1: ratio < threshold. `ucb`/`lcb` are one-sided `level` bounds
    exp(mean ± t_{level, n-1} · sd / sqrt(n)) of the per-block log ratios. Verdict:
    supported if ucb < threshold, rejected if lcb >= threshold, else inconclusive.
    `p_one_sided` is the t-test p for H0 (p < 1 - level exactly when ucb < threshold).
    """
    x = np.asarray(log_r, dtype=float)
    n = len(x)
    if n < 2:
        nan = float("nan")
        return {
            "n": n,
            "ratio": nan,
            "ucb": nan,
            "lcb": nan,
            "p_one_sided": nan,
            "verdict": "inconclusive",
        }
    m, se = float(x.mean()), float(x.std(ddof=1) / np.sqrt(n))
    q = float(stats.t.ppf(level, n - 1))
    ucb, lcb = float(np.exp(m + q * se)), float(np.exp(m - q * se))
    thr = float(np.log(threshold_ratio))
    p = float(stats.t.cdf((m - thr) / se, n - 1)) if se > 0 else float(m < thr)
    verdict = (
        "supported"
        if ucb < threshold_ratio
        else ("rejected" if lcb >= threshold_ratio else "inconclusive")
    )
    return {
        "n": n,
        "ratio": float(np.exp(m)),
        "ucb": ucb,
        "lcb": lcb,
        "p_one_sided": p,
        "verdict": verdict,
    }


def paired(df: pd.DataFrame, metric: str) -> pd.DataFrame:
    """One row per (size, load) cell with paired effect sizes and unadjusted tests."""
    rows = []
    for (size, load), g in df.groupby(["size", "load"]):
        w = g.pivot_table(index="rep", columns="mode", values=metric, aggfunc="first")
        w = w.dropna(subset=[c for c in ("plain", "aead") if c in w.columns])
        if not {"plain", "aead"} <= set(w.columns) or len(w) == 0:
            continue
        a, p = w["aead"].to_numpy(float), w["plain"].to_numpy(float)
        n = len(w)
        lr, d = np.log(a / p), a - p
        lo, hi = t_ci(lr, 0.95)
        dlo, dhi = t_ci(d, 0.95)
        p_t = (
            float(stats.ttest_1samp(lr, 0.0).pvalue)
            if n >= 2 and lr.std(ddof=1) > 0
            else float("nan")
        )
        rows.append(
            {
                "size": size,
                "load": load,
                "load_order": g.load_order.iloc[0],
                "n_blocks": n,
                "plain_median": float(np.median(p)),
                "aead_median": float(np.median(a)),
                "ratio": float(np.exp(lr.mean())),
                "ratio_ci95_lo": float(np.exp(lo)),
                "ratio_ci95_hi": float(np.exp(hi)),
                "_lr": lr,
                "delta": float(d.mean()),
                "delta_ci95_lo": dlo,
                "delta_ci95_hi": dhi,
                "p_t": p_t,
                "blocks_aead_higher": int((a > p).sum()),
                "p_signflip_exact": sign_flip_p(lr) if n >= 1 else float("nan"),
                "p_mannwhitney_exact": float(
                    stats.mannwhitneyu(a, p, alternative="two-sided", method="exact").pvalue
                )
                if n >= 2
                else float("nan"),
                "cliffs_delta": cliffs_delta(a, p),
            }
        )
    return pd.DataFrame(rows)


def valid_runs(df: pd.DataFrame) -> pd.Series:
    """Runs eligible for inference: k6 exit 0 and client CPU measured below the threshold.

    In the open-loop experiment k6 exits 99 on any dropped iteration, so such runs are
    excluded too. Exclusions are counted per cell in the output (`excluded_runs`) because
    they can bias the surviving pairs if one mode is excluded more often.
    """
    return (df.k6_exit_code == 0) & df.client_ok.fillna(False).astype(bool)


def analyze(df: pd.DataFrame) -> pd.DataFrame:
    planned = df[["size", "load"]].drop_duplicates()
    m_planned = len(planned)
    ok = valid_runs(df)
    excluded = df[~ok].groupby(["size", "load"]).size().rename("excluded_runs").reset_index()
    df = df[ok]
    open_loop = (df.executor == "constant-arrival-rate").any()
    out = []
    for metric, family, role in METRICS:
        if open_loop and metric == "rps":
            continue
        t = paired(df, metric)
        if t.empty:
            continue
        # Planned family size: every cell of the design, whether or not it yielded a pair.
        m = m_planned
        t["p_holm"] = holm(t.p_t.tolist(), m)
        # Simultaneous (Bonferroni) CI for the ratio across the m cells of this family.
        sim = []
        for lr in t._lr:
            lo, hi = t_ci(lr, 1 - ALPHA / m)
            sim.append((np.exp(lo), np.exp(hi)))
        t["ratio_simci_lo"], t["ratio_simci_hi"] = zip(*sim, strict=True)
        t["reject_h0_holm"] = t.p_holm < ALPHA
        t.insert(0, "metric", metric)
        t.insert(1, "family", family)
        t.insert(2, "role", role)
        t.insert(3, "family_size", m)
        t.insert(4, "cells_tested", len(t))
        t = t.merge(excluded, on=["size", "load"], how="left")
        t["excluded_runs"] = t.excluded_runs.fillna(0).astype(int)
        out.append(t.drop(columns="_lr"))
    res = pd.concat(out, ignore_index=True)
    res["pct_change"] = (res.ratio - 1) * 100
    return res.sort_values(
        ["role", "family", "size", "load_order"],
        key=lambda s: s.map(SIZE_ORDER) if s.name == "size" else s,
    )


def size_trend(df: pd.DataFrame) -> pd.DataFrame:
    """H4: slope of Δp50 (ms) on log2(payload bytes), per load level, respecting blocks.

    Within each block, the OLS slope of Δp50 = aead - plain across the payload sizes is
    computed. The block slopes are the independent observations: their mean is the
    estimate, with a t-based 95% CI and a one-sample t-test (df = blocks - 1). Pooling all
    block x size points into one regression would treat points from the same block as
    independent and understate the uncertainty.
    """
    df = df[valid_runs(df)]
    rows = []
    for load, g in df.groupby("load"):
        w = g.pivot_table(index=["rep", "size"], columns="mode", values="lat_p50_ms").dropna()
        slopes = []
        for _, b in w.groupby(level="rep"):
            sizes = b.index.get_level_values("size")
            if sizes.nunique() < 2:
                continue
            x = np.log2([SIZE_BYTES[s] for s in sizes])
            slopes.append(np.polyfit(x, (b["aead"] - b["plain"]).to_numpy(float), 1)[0])
        if not slopes:
            continue
        sl = np.array(slopes)
        lo, hi = t_ci(sl, 0.95)
        p = (
            float(stats.ttest_1samp(sl, 0.0).pvalue)
            if len(sl) >= 2 and sl.std(ddof=1) > 0
            else float("nan")
        )
        rows.append(
            {
                "load": load,
                "load_order": g.load_order.iloc[0],
                "n_blocks": len(sl),
                "slope_ms_per_doubling": float(sl.mean()),
                "slope_ci95_lo": lo,
                "slope_ci95_hi": hi,
                "p_slope": p,
            }
        )
    t = pd.DataFrame(rows)
    if not t.empty:
        t["p_holm"] = holm(t.p_slope.tolist(), df.load.nunique())
        t = t.sort_values("load_order")
    return t


def to_markdown(res: pd.DataFrame, trend: pd.DataFrame, raw: Path) -> str:
    f = lambda x, d=3: "n/a" if pd.isna(x) else f"{x:.{d}f}"  # noqa: E731
    nmax = int(res.n_blocks.max()) if len(res) else 0
    lines = [
        f"# Run-level statistics: `{raw.name}`",
        "",
        f"Unit of analysis: one run. Pairs: aead vs plain within each randomized block "
        f"(up to n = {nmax}). Ratio = geometric mean of aead/plain; CI95 = paired t on log "
        f"ratio; SimCI = Bonferroni-simultaneous CI within the family; p_Holm = Holm-adjusted "
        f"paired t-test p within the family (α = {ALPHA}).",
        "",
    ]
    if nmax < 3:
        lines += [
            f"> **n = {nmax} blocks: pipeline check only.** Intervals and p-values at this n "
            "have almost no power and are not used for inference.",
            "",
        ]
    for (role, family), g in res.groupby(["role", "family"], sort=False):
        lines += [
            f"## {family} ({role}, family of {int(g.family_size.iloc[0])})",
            "",
            "| size | load | n | plain | aead | ratio (Δ%) | CI95 | SimCI | Δ abs (CI95) "
            "| aead>plain | p_t | p_Holm | sign-flip p | MW p | Cliff δ |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for r in g.itertuples():
            lines.append(
                f"| {r.size} | {r.load} | {r.n_blocks} | {f(r.plain_median)} | "
                f"{f(r.aead_median)} | {f(r.ratio)} ({f(r.pct_change, 1)}%) | "
                f"[{f(r.ratio_ci95_lo)}, {f(r.ratio_ci95_hi)}] | "
                f"[{f(r.ratio_simci_lo)}, {f(r.ratio_simci_hi)}] | "
                f"{f(r.delta)} [{f(r.delta_ci95_lo)}, {f(r.delta_ci95_hi)}] | "
                f"{r.blocks_aead_higher}/{r.n_blocks} | {f(r.p_t, 4)} | {f(r.p_holm, 4)} | "
                f"{f(r.p_signflip_exact, 4)} | {f(r.p_mannwhitney_exact, 4)} | "
                f"{f(r.cliffs_delta, 2)} |"
            )
        lines.append("")
    if not trend.empty:
        lines += [
            "## H4: Δp50 vs payload size (OLS on log2 bytes, per load level)",
            "",
            "| load | blocks | slope (ms per doubling) | CI95 | p | p_Holm |",
            "|---|---|---|---|---|---|",
        ]
        for r in trend.itertuples():
            lines.append(
                f"| {r.load} | {r.n_blocks} | {f(r.slope_ms_per_doubling, 4)} | "
                f"[{f(r.slope_ci95_lo, 4)}, {f(r.slope_ci95_hi, 4)}] | {f(r.p_slope, 4)} | "
                f"{f(r.p_holm, 4)} |"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    raw = Path(sys.argv[1]).resolve()
    cfg = tomllib.loads((ROOT / "experiment.toml").read_text())
    runs = per_run(raw, cfg)
    res = analyze(runs)
    trend = size_trend(runs)
    out = ROOT / "results" / "processed" / raw.name
    out.mkdir(parents=True, exist_ok=True)
    res.to_csv(out / "stats.csv", index=False)
    trend.to_csv(out / "stats_size_trend.csv", index=False)
    md = to_markdown(res, trend, raw)
    (out / "stats.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
