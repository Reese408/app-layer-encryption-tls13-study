"""Static, offline results dashboard (one self-contained HTML file, no external assets).

Reads ONLY results/raw/<dir>/ (through aggregate.py and stats.py) plus the optional
validation verdict written by validate_pipeline.py, and writes
results/processed/dashboard/index.html. Charts are inline SVG with native hover
tooltips; every chart has a table view. Light and dark themes follow the OS setting.

Usage: analysis/dashboard.py results/raw/<dir> [results/raw/<dir> ...]
"""

from __future__ import annotations

import html
import json
import math
import sys
import tomllib
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aggregate import ROOT, SIZE_ORDER, per_run  # noqa: E402
from stats import analyze, size_trend  # noqa: E402

MODES = ("plain", "aead")
SIZE_LABEL = {"1k": "1 KiB", "16k": "16 KiB", "256k": "256 KiB"}
W = 760  # chart width (px, viewBox units)

CSS = """
.viz-root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;
--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a;
--good:#006300;--bad:#d03b3b}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])) .viz-root{
color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;
--grid:#2c2c2a;--axis:#383835;--s1:#3987e5;--s2:#d95926;--s3:#199e70;--good:#0ca30c;--bad:#e66767}}
:root[data-theme="dark"] .viz-root{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;
--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;--s1:#3987e5;--s2:#d95926;--s3:#199e70;
--good:#0ca30c;--bad:#e66767}
body{margin:0}
.viz-root{background:var(--page);color:var(--ink);font:14px/1.45 system-ui,-apple-system,sans-serif;
padding:24px 32px 64px;min-height:100vh}
h1{font-size:22px;margin:0 0 4px}h2{font-size:18px;margin:40px 0 4px;padding-top:16px;
border-top:1px solid var(--grid)}h3{font-size:15px;margin:28px 0 2px}
p.note,.sub{color:var(--ink2);margin:2px 0 10px;max-width:960px}
.tiles{display:flex;gap:12px;flex-wrap:wrap;margin:14px 0}
.tile{background:var(--surface);border:1px solid var(--grid);border-radius:8px;padding:10px 14px;
min-width:140px}.tile .v{font-size:24px;font-weight:600}.tile .k{color:var(--ink2);font-size:12px}
.card{background:var(--surface);border:1px solid var(--grid);border-radius:8px;padding:12px 14px;
margin:10px 0;max-width:820px}
svg{display:block;max-width:100%;height:auto}svg text{fill:var(--ink2);font-size:11px}
svg .t1{fill:var(--ink);font-size:12px}svg .grid{stroke:var(--grid);stroke-width:1}
svg .axis{stroke:var(--axis);stroke-width:1}svg .ref{stroke:var(--muted);stroke-width:1;
stroke-dasharray:3 3}
.legend{display:flex;gap:16px;color:var(--ink2);font-size:12px;margin:2px 0 6px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px;
vertical-align:-1px}
details{margin:6px 0}summary{cursor:pointer;color:var(--ink2);font-size:12px}
table{border-collapse:collapse;font-size:12px;margin:6px 0}
th,td{border-bottom:1px solid var(--grid);padding:3px 8px;text-align:right;white-space:nowrap}
th{color:var(--ink2);font-weight:600}td:first-child,th:first-child{text-align:left}
.pass{color:var(--good);font-weight:600}.fail{color:var(--bad);font-weight:600}
code{font-size:12px}
"""


def esc(x: object) -> str:
    return html.escape(str(x))


def fmt(x: float, d: int = 3) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{d}f}"


def table(df: pd.DataFrame, digits: int = 3) -> str:
    head = "".join(f"<th>{esc(c)}</th>" for c in df.columns)
    rows = []
    for r in df.itertuples(index=False):
        cells = []
        for v in r:
            if isinstance(v, float):
                cells.append(f"<td>{fmt(v, digits)}</td>")
            elif isinstance(v, bool):
                cls = "pass" if v else "fail"
                cells.append(f'<td class="{cls}">{"✓ pass" if v else "✗ fail"}</td>')
            else:
                cells.append(f"<td>{esc(v)}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def details(label: str, body: str) -> str:
    return f"<details><summary>{esc(label)}</summary>{body}</details>"


def legend() -> str:
    return (
        '<div class="legend"><span><i style="background:var(--s1)"></i>plain (TLS 1.3 only)'
        '</span><span><i style="background:var(--s2)"></i>aead (TLS 1.3 + AES-256-GCM envelope)'
        "</span></div>"
    )


def log_ticks(lo: float, hi: float) -> list[float]:
    ticks = []
    for e in range(math.floor(math.log10(lo)), math.ceil(math.log10(hi)) + 1):
        for m in (1, 2, 5):
            v = m * 10**e
            if lo <= v <= hi:
                ticks.append(v)
    return ticks


def log_map(v: float, lo: float, hi: float, a: float, b: float) -> float:
    """Map v in [lo, hi] on a log scale to [a, b]."""
    return a + (math.log(v) - math.log(lo)) / (math.log(hi) - math.log(lo)) * (b - a)


def tick_label(v: float) -> str:
    return f"{v:g}"


def forest(res: pd.DataFrame, metric: str, title: str) -> str:
    """Effect-size plot: aead/plain ratio per cell, 95% CI (solid) and simultaneous CI (faint)."""
    t = res[res.metric == metric]
    if t.empty:
        return ""
    row_h, top, left, right = 26, 26, 150, 210
    h = top + row_h * len(t) + 30
    vals = pd.concat([t.ratio_ci95_lo, t.ratio_ci95_hi, t.ratio]).replace(
        [float("inf"), -float("inf")], float("nan")
    )
    vals = vals[(vals > 0) & vals.notna()]
    lo = min(vals.min(), 0.95) * 0.95
    hi = max(vals.max(), 1.05) * 1.05
    lo, hi = max(lo, 0.2), min(hi, 5.0)

    def x(v: float) -> float:
        return log_map(min(max(v, lo), hi), lo, hi, left, W - right)

    parts = [f'<svg viewBox="0 0 {W} {h}" role="img" aria-label="{esc(title)}">']
    ratio_ticks = [0.2, 0.25, 1 / 3, 0.5, 2 / 3, 0.8, 0.9, 1.1, 1.25, 1.5, 2, 3, 4, 5]
    kept: list[float] = []
    for tk in sorted((v for v in ratio_ticks if lo <= v <= hi), key=lambda v: abs(math.log(v))):
        if all(abs(x(tk) - x(k)) >= 46 for k in kept):
            kept.append(tk)
    for v in kept:
        parts.append(
            f'<line class="grid" x1="{x(v):.1f}" x2="{x(v):.1f}" y1="{top - 6}" '
            f'y2="{h - 24}"/><text x="{x(v):.1f}" y="{h - 8}" text-anchor="middle">'
            f"{v:.3g}×</text>"
        )
    parts.append(
        f'<line class="ref" x1="{x(1):.1f}" x2="{x(1):.1f}" y1="{top - 10}" y2="{h - 24}"/>'
        f'<text x="{x(1):.1f}" y="{top - 14}" text-anchor="middle">1× (no difference)</text>'
    )
    for i, r in enumerate(t.itertuples()):
        y = top + i * row_h + row_h / 2
        tip = (
            f"{SIZE_LABEL[r.size]}, {r.load}: aead/plain = {r.ratio:.3f} "
            f"({r.pct_change:+.1f}%), 95% CI [{fmt(r.ratio_ci95_lo)}, {fmt(r.ratio_ci95_hi)}], "
            f"simultaneous CI [{fmt(r.ratio_simci_lo)}, {fmt(r.ratio_simci_hi)}], "
            f"p_Holm = {fmt(r.p_holm, 4)}, n = {r.n_blocks} blocks"
        )
        parts.append(f"<g><title>{esc(tip)}</title>")
        parts.append(
            f'<rect x="0" y="{y - row_h / 2:.1f}" width="{W}" height="{row_h}" fill="transparent"/>'
            f'<text class="t1" x="8" y="{y + 4:.1f}">{SIZE_LABEL[r.size]} · {r.load}</text>'
        )
        if not math.isnan(r.ratio_simci_lo):
            parts.append(
                f'<line x1="{x(r.ratio_simci_lo):.1f}" x2="{x(r.ratio_simci_hi):.1f}" '
                f'y1="{y:.1f}" y2="{y:.1f}" stroke="var(--s2)" stroke-opacity=".3" '
                f'stroke-width="6" stroke-linecap="round"/>'
            )
            # An interval that extends past the axis is drawn to the edge with an arrowhead.
            for end, edge, d in ((r.ratio_simci_lo, lo, -1), (r.ratio_simci_hi, hi, 1)):
                if (end < edge) if d < 0 else (end > edge):
                    ex = x(edge)
                    parts.append(
                        f'<path d="M{ex - 7 * d:.1f},{y - 5:.1f} L{ex:.1f},{y:.1f} '
                        f'L{ex - 7 * d:.1f},{y + 5:.1f}" fill="none" stroke="var(--s2)" '
                        f'stroke-width="2"><title>interval extends beyond the axis</title></path>'
                    )
        if not math.isnan(r.ratio_ci95_lo):
            parts.append(
                f'<line x1="{x(r.ratio_ci95_lo):.1f}" x2="{x(r.ratio_ci95_hi):.1f}" '
                f'y1="{y:.1f}" y2="{y:.1f}" stroke="var(--s2)" stroke-width="2"/>'
            )
        sig = "" if math.isnan(r.p_holm) else (" ✓" if r.p_holm < 0.05 else "")
        ci_label = (
            "[CI n/a]"
            if math.isnan(r.ratio_ci95_lo)
            else f"[{(r.ratio_ci95_lo - 1) * 100:+.0f}, {(r.ratio_ci95_hi - 1) * 100:+.0f}]"
        )
        parts.append(
            f'<circle cx="{x(r.ratio):.1f}" cy="{y:.1f}" r="5" fill="var(--s2)" '
            f'stroke="var(--surface)" stroke-width="2"/>'
            f'<text class="t1" x="{W - right + 12}" y="{y + 4:.1f}">{r.pct_change:+.1f}% '
            f"{ci_label}{sig}</text>"
            "</g>"
        )
    parts.append("</svg>")
    cols = [
        "size",
        "load",
        "n_blocks",
        "plain_median",
        "aead_median",
        "ratio",
        "ratio_ci95_lo",
        "ratio_ci95_hi",
        "ratio_simci_lo",
        "ratio_simci_hi",
        "delta",
        "delta_ci95_lo",
        "delta_ci95_hi",
        "p_t",
        "p_holm",
        "blocks_aead_higher",
        "p_signflip_exact",
        "p_mannwhitney_exact",
        "cliffs_delta",
    ]
    return (
        f"<h3>{esc(title)}</h3><p class='sub'>Point = geometric-mean ratio aead/plain over "
        "blocks; dark bar = 95% CI; faint bar = Bonferroni-simultaneous CI for the family (arrowhead = extends beyond the axis). "
        "Right: % change [95% CI]; ✓ = Holm-adjusted p &lt; 0.05. Log scale.</p>"
        f'<div class="card">{"".join(parts)}'
        f"{details('Table view', table(t[cols], 4))}</div>"
    )


def run_dots(runs: pd.DataFrame, metric: str, title: str, unit: str) -> str:
    """Every run as a dot, plain vs aead side by side, per cell. Log y."""
    cells = (
        runs[["size", "load", "load_order"]]
        .drop_duplicates()
        .sort_values(
            ["load_order", "size"], key=lambda s: s.map(SIZE_ORDER) if s.name == "size" else s
        )
    )
    v = runs[metric][runs[metric] > 0]
    if v.empty:
        return ""
    lo, hi = v.min() / 1.3, v.max() * 1.3
    h, top, bottom, left = 280, 16, 44, 56
    col_w = (W - left - 10) / len(cells)

    def y(val: float) -> float:
        return log_map(val, lo, hi, h - bottom, top)

    parts = [f'<svg viewBox="0 0 {W} {h}" role="img" aria-label="{esc(title)}">']
    for tv in log_ticks(lo, hi):
        parts.append(
            f'<line class="grid" x1="{left}" x2="{W - 10}" y1="{y(tv):.1f}" y2="{y(tv):.1f}"/>'
            f'<text x="{left - 6}" y="{y(tv) + 4:.1f}" text-anchor="end">{tick_label(tv)}</text>'
        )
    parts.append(
        f'<text x="12" y="{top + (h - top - bottom) / 2:.0f}" transform="rotate(-90 12 '
        f'{top + (h - top - bottom) / 2:.0f})" text-anchor="middle">{esc(unit)}</text>'
    )
    for i, c in enumerate(cells.itertuples()):
        cx = left + col_w * (i + 0.5)
        parts.append(
            f'<text class="t1" x="{cx:.1f}" y="{h - 24}" text-anchor="middle">'
            f"{SIZE_LABEL[c.size]}</text>"
            f'<text x="{cx:.1f}" y="{h - 9}" text-anchor="middle">{c.load}</text>'
        )
        for j, mode in enumerate(MODES):
            g = runs[(runs["size"] == c.size) & (runs.load == c.load) & (runs["mode"] == mode)]
            mx = cx + (j - 0.5) * min(26, col_w / 3)
            for k, r in enumerate(g.sort_values("rep").itertuples()):
                val = getattr(r, metric)
                if not val or val <= 0 or math.isnan(val):
                    continue
                jitter = (k - (len(g) - 1) / 2) * 2.2
                tip = f"{mode}, {SIZE_LABEL[c.size]}, {c.load}, block {r.rep}: {val:.4g} {unit}"
                parts.append(
                    f'<circle cx="{mx + jitter:.1f}" cy="{y(val):.1f}" r="4.5" '
                    f'fill="var(--s{j + 1})" stroke="var(--surface)" stroke-width="2">'
                    f"<title>{esc(tip)} ({esc(r.run_id)})</title></circle>"
                )
    parts.append("</svg>")
    tv = runs.pivot_table(
        index=["size", "load", "rep"], columns="mode", values=metric
    ).reset_index()
    return (
        f"<h3>{esc(title)}</h3><p class='sub'>Each dot is one run (one block). Log scale.</p>"
        f'<div class="card">{legend()}{"".join(parts)}{details("Table view", table(tv, 4))}</div>'
    )


def ecdf(raw: Path, runs: pd.DataFrame, title: str) -> str:
    """Sampled per-request latency, plain vs aead, per size at the lowest load level."""
    load = runs.sort_values("load_order").load.iloc[0]
    sizes = sorted(runs["size"].unique(), key=SIZE_ORDER.get)
    pw, h, top, bottom = (W - 20) / len(sizes), 230, 14, 40
    parts = [f'<svg viewBox="0 0 {W} {h}" role="img" aria-label="{esc(title)}">']
    rows = []
    any_data = False
    for si, size in enumerate(sizes):
        x0 = 10 + si * pw + 34
        x1 = 10 + (si + 1) * pw - 10
        data = {}
        for mode in MODES:
            ids = runs[(runs["size"] == size) & (runs.load == load) & (runs["mode"] == mode)].run_id
            frames = [
                pd.read_csv(raw / f"{rid}.samples.csv")
                for rid in ids
                if (raw / f"{rid}.samples.csv").exists()
            ]
            if frames:
                s = pd.concat(frames).req_ms.dropna().sort_values().to_numpy()
                if len(s):
                    data[mode] = s
        if not data:
            continue
        any_data = True
        allv = [v for s in data.values() for v in (s.min(), s.max())]
        lo, hi = min(allv) / 1.1, max(allv) * 1.1

        def xf(v: float, lo: float = lo, hi: float = hi, x0: float = x0, x1: float = x1) -> float:
            return log_map(v, lo, hi, x0, x1)

        def yf(q: float) -> float:
            return top + (1 - q) * (h - top - bottom)

        for q in (0, 0.5, 0.9, 1.0):
            parts.append(
                f'<line class="grid" x1="{x0}" x2="{x1}" y1="{yf(q):.1f}" y2="{yf(q):.1f}"/>'
            )
            if si == 0:
                parts.append(
                    f'<text x="{x0 - 4}" y="{yf(q) + 4:.1f}" text-anchor="end">{q:g}</text>'
                )
        for tv in log_ticks(lo, hi)[:: max(1, len(log_ticks(lo, hi)) // 4)]:
            parts.append(
                f'<text x="{xf(tv):.1f}" y="{h - 24}" text-anchor="middle">{tick_label(tv)}</text>'
            )
        parts.append(
            f'<text class="t1" x="{(x0 + x1) / 2:.1f}" y="{h - 6}" text-anchor="middle">'
            f"{SIZE_LABEL[size]} · {load} (ms)</text>"
        )
        for j, mode in enumerate(MODES):
            s = data.get(mode)
            if s is None:
                continue
            idx = sorted({int(i) for i in [k * (len(s) - 1) / 200 for k in range(201)]})
            pts = " ".join(f"{xf(s[i]):.1f},{yf((i + 1) / len(s)):.1f}" for i in idx)
            p50, p99 = s[int(0.5 * (len(s) - 1))], s[int(0.99 * (len(s) - 1))]
            parts.append(
                f'<polyline points="{pts}" fill="none" stroke="var(--s{j + 1})" stroke-width="2">'
                f"<title>{esc(f'{mode}, {SIZE_LABEL[size]}: n = {len(s)} sampled requests, sample p50 = {p50:.3f} ms, p99 = {p99:.3f} ms')}</title></polyline>"
            )
            rows.append(
                {
                    "size": size,
                    "load": load,
                    "mode": mode,
                    "n_sampled": len(s),
                    "sample_p50_ms": p50,
                    "sample_p90_ms": s[int(0.9 * (len(s) - 1))],
                    "sample_p99_ms": p99,
                }
            )
    parts.append("</svg>")
    if not any_data:
        return ""
    return (
        f"<h3>{esc(title)}</h3><p class='sub'><b>Deterministic 1-in-K sample of measured "
        "requests, pooled over blocks; not the full request population.</b> Exact percentiles "
        "are in the k6 summaries and the tables above. y = cumulative fraction; log x.</p>"
        f'<div class="card">{legend()}{"".join(parts)}'
        f"{details('Table view', table(pd.DataFrame(rows), 4))}</div>"
    )


def breakdown(cells: pd.DataFrame, title: str) -> str:
    """Server compute per request in aead mode: workload vs crypto (decrypt + encrypt)."""
    a = cells[cells["mode"] == "aead"].copy()
    if a.empty or a.srv_work_avg_ms.isna().all():
        return ""
    a["crypto"] = a.srv_dec_avg_ms + a.srv_enc_avg_ms
    a = a.sort_values(
        ["load_order", "size"], key=lambda s: s.map(SIZE_ORDER) if s.name == "size" else s
    )
    row_h, top, left, right = 24, 8, 130, 170
    h = top + row_h * len(a) + 10
    parts = [f'<svg viewBox="0 0 {W} {h}" role="img" aria-label="{esc(title)}">']
    for i, r in enumerate(a.itertuples()):
        y = top + i * row_h
        total = r.crypto + r.srv_work_avg_ms
        span = W - left - right
        wc = span * r.crypto / total
        share = r.crypto / total * 100
        tip = (
            f"{SIZE_LABEL[r.size]}, {r.load}: decrypt {r.srv_dec_avg_ms:.4f} ms + encrypt "
            f"{r.srv_enc_avg_ms:.4f} ms = {r.crypto:.4f} ms; workload {r.srv_work_avg_ms:.4f} ms"
        )
        parts.append(
            f"<g><title>{esc(tip)}</title>"
            f'<text class="t1" x="8" y="{y + 15}">{SIZE_LABEL[r.size]} · {r.load}</text>'
            f'<rect x="{left}" y="{y + 4}" width="{max(wc - 1, 1):.1f}" height="14" rx="3" '
            f'fill="var(--s3)"/><rect x="{left + wc + 1:.1f}" y="{y + 4}" '
            f'width="{max(span - wc - 1, 1):.1f}" height="14" rx="3" fill="var(--muted)" '
            f'fill-opacity=".45"/><text class="t1" x="{W - right + 10}" y="{y + 15}">'
            f"crypto {share:.0f}% · {total:.3f} ms</text></g>"
        )
    parts.append("</svg>")
    tv = a[["size", "load", "srv_dec_avg_ms", "srv_work_avg_ms", "srv_enc_avg_ms", "crypto"]]
    return (
        f"<h3>{esc(title)}</h3><p class='sub'>Mean server time per request from the "
        "<code>Server-Timing</code> header, medians across blocks. Bars are normalised to "
        "100% of server compute (decrypt + workload + encrypt).</p>"
        '<div class="card"><div class="legend"><span><i style="background:var(--s3)">'
        '</i>decrypt + encrypt</span><span><i style="background:var(--muted)"></i>workload'
        f"</span></div>{''.join(parts)}{details('Table view', table(tv, 4))}</div>"
    )


def validation_block(raw: Path) -> str:
    p = ROOT / "results" / "processed" / raw.name / "validation.json"
    if not p.exists():
        return "<p class='note'>No validation verdict (run <code>make check-run</code>).</p>"
    v = json.loads(p.read_text())["summary"]
    ok = v["runs_passing"] == v["runs"]
    rows = pd.DataFrame(
        [
            {"criterion": k, "runs passing": c, "pass": c.split("/")[0] == c.split("/")[1]}
            for k, c in v["criteria"].items()
        ]
    )
    extra = {k: val for k, val in v.items() if k not in ("criteria", "raw")}
    return (
        f"<h3>Pipeline acceptance criteria</h3><p class='sub'>From "
        f"<code>validate_pipeline.py</code>. Overall: <span class='{'pass' if ok else 'fail'}'>"
        f"{v['runs_passing']}/{v['runs']} runs pass every criterion</span>.</p>"
        f'<div class="card">{table(rows)}'
        f"{details('Summary values', '<pre>' + esc(json.dumps(extra, indent=2)) + '</pre>')}</div>"
    )


def section(raw: Path, cfg: dict) -> str:
    from aggregate import compare, per_cell

    runs = per_run(raw, cfg)
    cells = per_cell(runs)
    cmp = compare(cells)
    res = analyze(runs)
    trend = size_trend(runs)
    man = json.loads((raw / "manifest.json").read_text())
    open_loop = (runs.executor == "constant-arrival-rate").any()
    kind = "Open loop (fixed arrival rate)" if open_loop else "Closed loop (constant VUs)"
    nblocks = int(runs.rep.nunique())
    ok = runs.k6_exit_code.eq(0).all() and runs.error_rate.eq(0).all()
    tiles = [
        ("runs", f"{len(runs)}"),
        ("blocks (repetitions)", f"{nblocks}"),
        ("k6 exit 0", f"{int(runs.k6_exit_code.eq(0).sum())}/{len(runs)}"),
        ("max error rate", f"{runs.error_rate.max():.4f}"),
        ("min integrity", f"{runs.integrity_rate.min():.4f}"),
        ("max k6 CPU (of limit)", f"{runs.k6_cpu_frac_of_limit.max() * 100:.0f}%"),
    ]
    if open_loop:
        tiles.append(("dropped iterations (total)", f"{int(runs.dropped_iterations.sum())}"))
    out = [
        f"<h2>{esc(kind)}: <code>{esc(raw.name)}</code></h2>",
        f"<p class='note'>Profile <code>{esc(man['profile'])}</code>, commit "
        f"<code>{esc(man['git']['commit'][:7])}</code>{' (dirty)' if man['git']['dirty'] else ''}"
        f", started {esc(man['started_utc'][:19])} UTC, host power: "
        f"{esc(man['host'].get('power', ''))}.</p>",
        '<div class="tiles">'
        + "".join(
            f'<div class="tile"><div class="v">{esc(v)}</div><div class="k">{esc(k)}</div></div>'
            for k, v in tiles
        )
        + "</div>",
    ]
    if not ok:
        out.append("<p class='fail'>Some runs failed; see the per-run table.</p>")
    if nblocks < 3:
        out.append(
            f"<p class='note'><b>n = {nblocks} blocks: pipeline check only.</b> Intervals and "
            "p-values at this n have almost no power and support no inference.</p>"
        )
    out.append(validation_block(raw))
    out.append(forest(res, "lat_p50_ms", "Median latency (http_req_duration p50): aead vs plain"))
    out.append(forest(res, "lat_p99_ms", "p99 latency: aead vs plain"))
    if not open_loop:
        out.append(forest(res, "rps", "Throughput: aead vs plain"))
    out.append(forest(res, "api_cpu_ms_per_req", "API CPU-ms per request: aead vs plain"))
    out.append(run_dots(runs, "lat_p50_ms", "Median latency per run", "ms"))
    out.append(run_dots(runs, "api_mem_mib_max", "Peak API working set per run", "MiB"))
    out.append(breakdown(cells, "Where server time goes (aead)"))
    out.append(ecdf(raw, runs, "Per-request latency distribution (sampled)"))
    if not trend.empty:
        out.append(
            "<h3>H4: Δp50 vs payload size</h3>"
            f'<div class="card">{table(trend.drop(columns="load_order"), 4)}</div>'
        )
    run_cols = [
        "run_id",
        "mode",
        "size",
        "load",
        "rep",
        "k6_exit_code",
        "requests",
        "rps",
        "dropped_iterations",
        "lat_p50_ms",
        "lat_p99_ms",
        "api_cpu_pct",
        "api_idle_mem_mib",
        "api_mem_mib_max",
        "k6_cpu_frac_of_limit",
    ]
    out.append(details("All runs", table(runs[[c for c in run_cols if c in runs]], 3)))
    out.append(details("Cell comparison", table(cmp.drop(columns="load_order"), 3)))
    return "\n".join(out)


def main() -> None:
    raws = [Path(a).resolve() for a in sys.argv[1:]]
    if not raws:
        sys.exit("usage: dashboard.py results/raw/<dir> [...]")
    cfg = tomllib.loads((ROOT / "experiment.toml").read_text())
    body = "\n".join(section(r, cfg) for r in raws)
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AES-256-GCM over TLS 1.3: results dashboard</title><style>{CSS}</style></head>
<body><main class="viz-root"><h1>Application-layer AES-256-GCM over TLS 1.3: results dashboard</h1>
<p class="note">Generated by <code>analysis/dashboard.py</code> from <code>results/raw/</code> only.
Unit of analysis: one run; aead and plain paired within randomized blocks. Hover any mark for
its exact values; every chart has a table view.</p>
{body}</main></body></html>"""
    out = ROOT / "results" / "processed" / "dashboard"
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(page)
    print(out / "index.html")


if __name__ == "__main__":
    main()
