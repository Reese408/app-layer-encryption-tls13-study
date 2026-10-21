"""Figure: H5, encryption overhead share during local LLM inference (Experiment C).

Source (produced by `make h5 RUN=results/raw/20261021T155308Z-infer`):
  results/processed/20261021T155308Z-infer/h5.csv       pre-registered test, per length
  results/processed/20261021T155308Z-infer/h5_runs.csv  per-run (block) ratios

Plotted per response length, for the primary measure (end-to-end latency):
  - per-block share 1 - 1/r_b (open circles; n = 5 blocks), for transparency only;
  - point estimate 1 - 1/ratio, with ratio the geometric mean of r_b (filled square);
  - one-sided 95% upper confidence bound 1 - 1/UCB (bracket), the quantity tested;
  - the pre-specified 1% threshold (dashed line).

Output: reports/figures/fig_h5_inference_overhead.{svg,png}
"""

from __future__ import annotations

import pandas as pd
from paper_style import BLACK, DARK, MID, PROCESSED, plt, require, save

DATASET = "20261021T155308Z-infer"
HOW = f"make h5 RUN=results/raw/{DATASET}"
THRESHOLD_PCT = 1.0
LEVELS = [("short", "32 tokens"), ("long", "256 tokens")]


def load() -> tuple[pd.DataFrame, pd.DataFrame]:
    h5 = pd.read_csv(require(PROCESSED / DATASET / "h5.csv", HOW))
    runs = pd.read_csv(require(PROCESSED / DATASET / "h5_runs.csv", HOW))
    return h5[h5.measure == "e2e"].set_index("length"), runs[runs.valid]


def main() -> None:
    h5, runs = load()
    fig, ax = plt.subplots(figsize=(6.5, 2.7))
    offsets = [-0.10, -0.05, 0.0, 0.05, 0.10]  # fixed vertical spread of the block points
    for y, (length, _label) in enumerate(LEVELS):
        r = h5.loc[length]
        blocks = runs[runs.length == length].sort_values("rep")
        share_b = (1 - 1 / blocks.r_e2e) * 100
        ax.scatter(
            share_b,
            [y + 0.27 + o for o in offsets[: len(share_b)]],
            s=16,
            facecolors="white",
            edgecolors=MID,
            linewidths=0.8,
            zorder=3,
            label="Per-block share (individual runs)" if y == 0 else None,
        )
        est, ucb = r.share_pct, r.share_ucb_pct
        ax.plot([est, ucb], [y, y], color=BLACK, lw=1.2, zorder=2)
        ax.plot([ucb, ucb], [y - 0.09, y + 0.09], color=BLACK, lw=1.6, zorder=4)
        ax.plot([ucb - 0.03, ucb], [y - 0.09, y - 0.09], color=BLACK, lw=1.6, zorder=4)
        ax.plot([ucb - 0.03, ucb], [y + 0.09, y + 0.09], color=BLACK, lw=1.6, zorder=4)
        ax.scatter(
            [est],
            [y],
            marker="s",
            s=30,
            color=BLACK,
            zorder=5,
            label="Point estimate (geometric mean of blocks)" if y == 0 else None,
        )
        ax.annotate(
            f"estimate {est:+.3f}%".replace("-", "\u2212"),
            (est, y),
            xytext=(-8, 0),
            textcoords="offset points",
            ha="right",
            va="center",
            fontsize=8.5,
        )
        ax.annotate(
            f"upper bound\n{ucb:.3f}%",
            (ucb, y),
            xytext=(0, 9),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=8.5,
            linespacing=1.0,
        )
    # Legend proxy for the bound (drawn as a bracket above).
    ax.plot([], [], color=BLACK, lw=1.2, marker="|", markersize=9, mew=1.6,
            label="One-sided 95% upper confidence bound")  # fmt: skip
    ax.axvline(THRESHOLD_PCT, color=DARK, ls=(0, (5, 3)), lw=1.0, zorder=1)
    ax.text(
        THRESHOLD_PCT + 0.04,
        -0.5,
        "pre-specified\n1% threshold",
        fontsize=8.5,
        va="top",
        ha="left",
    )
    ax.axvline(0, color=MID, lw=0.6, zorder=0)
    n = int(h5.n_blocks.min())
    ax.set_yticks(range(len(LEVELS)), [f"{lab}\n(n = {n} blocks)" for _, lab in LEVELS])
    ax.set_ylim(-0.55, len(LEVELS) - 0.5)
    ax.invert_yaxis()
    lo = min(-2.0, float((1 - 1 / runs.r_e2e).min() * 100) - 0.2)
    ax.set_xlim(lo, 1.6)
    ax.set_xlabel("Encryption overhead share of end-to-end latency, Δ / T$_{\\mathrm{AEAD}}$ (%)")
    ax.legend(loc="lower left", bbox_to_anchor=(0.0, 1.0), ncol=3, handletextpad=0.4,
              columnspacing=1.2, borderaxespad=0.2)  # fmt: skip
    ax.tick_params(axis="y", length=0)
    ax.spines["left"].set_visible(False)
    for p in save(fig, "fig_h5_inference_overhead"):
        print(p)


if __name__ == "__main__":
    main()
