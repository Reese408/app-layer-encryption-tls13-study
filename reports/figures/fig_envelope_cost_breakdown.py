"""Figure: where the server-side envelope time goes (Experiment D, A1 microbenchmark).

Source (produced by `uv run python analysis/envelope_micro.py
results/raw/20261021T162323Z-envelope-micro`):
  results/processed/20261021T162323Z-envelope-micro/breakdown.csv

Descriptive only. For each container process (5), the share of an operation is its median
time over 25 repetitions divided by the median time of the whole seal or open operation; the
plotted value is the median of those per-process shares. The residual (whole minus the sum
of the timed components) is plotted as measured, including a small negative value; bars are
not normalised to 100%. Payload sizes: the 1, 16 and 256 KiB request plaintexts of
Experiment A. No confidence intervals: this is a microbenchmark, not an inferential test.

Output: reports/figures/fig_envelope_cost_breakdown.{svg,png}
"""

from __future__ import annotations

import pandas as pd
from paper_style import BLACK, DARK, LIGHT, MID, PROCESSED, WHITE, plt, require, save

DATASET = "20261021T162323Z-envelope-micro"
HOW = f"uv run python analysis/envelope_micro.py results/raw/{DATASET}"
SIZES = [("1k", "1 KiB"), ("16k", "16 KiB"), ("256k", "256 KiB")]
# (label, ops summed into the category, face, hatch) in fixed legend and stacking order.
CATS = [
    ("AES-GCM", {"aesgcm"}, DARK, ""),
    ("Base64", {"b64"}, LIGHT, "////"),
    ("JSON", {"json"}, WHITE, "...."),
    ("Nonce and AAD", {"urandom", "aad"}, MID, ""),
    ("Residual (whole − Σ components)", {"residual"}, WHITE, "xxxx"),
]
PATHS = [("seal", "Sealing (encrypt response)"), ("open", "Opening (decrypt request)")]
LABEL_MIN = 6.0  # % below which a segment is labelled beside the bar instead of inside


def load() -> pd.DataFrame:
    b = pd.read_csv(require(PROCESSED / DATASET / "breakdown.csv", HOW))
    b["kind"] = b.op.str.split(".").str[1]
    return b


def main() -> None:
    b = load()
    n_proc = int(b.n_proc.min())
    fig, axes = plt.subplots(2, 1, figsize=(6.5, 4.4), sharex=True)
    for ax, (path, title) in zip(axes, PATHS, strict=True):
        p = b[b.path == path]
        for y, (size, _label) in enumerate(SIZES):
            s = p[p["size"] == size]
            whole_us = float(s[s.kind == "whole"].us_median.iloc[0])
            left_pos, left_neg, small = 0.0, 0.0, []
            for name, kinds, face, hatch in CATS:
                rows = s[s.kind.isin(kinds)]
                if rows.empty:
                    continue
                v = float(rows.share_median.sum()) * 100
                if v >= 0:
                    x0, left_pos = left_pos, left_pos + v
                else:
                    left_neg += v
                    x0 = left_neg
                ax.barh(y, abs(v), left=x0, height=0.62, color=face, hatch=hatch,
                        edgecolor=BLACK, linewidth=0.6)  # fmt: skip
                short = name.split(" (")[0]
                if abs(v) >= LABEL_MIN:
                    ax.text(x0 + abs(v) / 2, y, f"{v:.1f}%", ha="center", va="center",
                            fontsize=8.5, bbox={"boxstyle": "square,pad=0.15", "fc": "white",
                                                "ec": "none"})  # fmt: skip
                else:
                    val = "<0.1%" if abs(v) < 0.05 else f"{v:+.1f}%".replace("-", "−")
                    small.append(f"{short[0].lower() + short[1:]} {val}")
            note = f"whole {whole_us:,.1f} µs"
            if small:
                note += "; " + ", ".join(small)
            ax.text(max(left_pos, 100) + 1.5, y, note, va="center", ha="left", fontsize=8)
        ax.set_yticks(range(len(SIZES)), [lab for _, lab in SIZES])
        ax.invert_yaxis()
        ax.set_title(title, loc="left", fontsize=10)
        ax.axvline(0, color=BLACK, lw=0.6)
        ax.axvline(100, color=MID, lw=0.6, ls=(0, (2, 2)))
        ax.tick_params(axis="y", length=0)
        ax.spines["left"].set_visible(False)
    axes[-1].set_xlim(-2, 100)
    axes[-1].set_xticks(range(0, 101, 20))
    axes[-1].set_xlabel("Share of the whole seal or open operation time (%)")
    handles = [
        plt.Rectangle((0, 0), 1, 1, facecolor=f, hatch=h, edgecolor=BLACK, linewidth=0.6)
        for _, _, f, h in CATS
    ]
    fig.legend(handles, [c[0] for c in CATS], loc="lower center", ncol=3,
               bbox_to_anchor=(0.45, -0.07), handlelength=1.6, columnspacing=1.2)  # fmt: skip
    fig.text(
        0.0,
        -0.10,
        f"Descriptive microbenchmark (no confidence intervals): median of per-process shares "
        f"across {n_proc} container processes,\n25 repetitions each; residual shown as "
        "measured (not forced to zero); bars are not normalised to 100%. Nonce and AAD "
        "apply to sealing only.",
        fontsize=7.5,
        ha="left",
        va="top",
    )
    fig.subplots_adjust(hspace=0.35, right=0.78)
    for out in save(fig, "fig_envelope_cost_breakdown"):
        print(out)


if __name__ == "__main__":
    main()
