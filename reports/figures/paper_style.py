"""Shared figure style for the paper: grayscale, white background, serif type.

Output is deterministic: SVG text stays editable (`svg.fonttype = none`), the SVG hash salt
is fixed, and creation dates and software tags are omitted from SVG and PNG metadata, so
re-running a figure script on the same processed data yields byte-identical files.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
FIG_DIR = Path(__file__).resolve().parent
PROCESSED = ROOT / "results" / "processed"

# Grayscale levels (0 = black, 1 = white), chosen to stay distinct when printed.
BLACK, DARK, MID, LIGHT, WHITE = "0.0", "0.25", "0.55", "0.82", "1.0"

plt.rcParams.update(
    {
        "font.family": "serif",
        "font.serif": ["Times New Roman", "Times", "STIXGeneral"],
        "mathtext.fontset": "stix",
        "font.size": 10,
        "axes.titlesize": 10,
        "axes.labelsize": 10,
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
        "legend.fontsize": 8.5,
        "axes.edgecolor": BLACK,
        "axes.labelcolor": BLACK,
        "text.color": BLACK,
        "xtick.color": BLACK,
        "ytick.color": BLACK,
        "axes.linewidth": 0.6,
        "xtick.major.width": 0.6,
        "ytick.major.width": 0.6,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "savefig.facecolor": "white",
        "hatch.linewidth": 0.5,
        "hatch.color": BLACK,
        "legend.frameon": False,
        "svg.fonttype": "none",
        "svg.hashsalt": "cyb485-figures",
    }
)


def require(path: Path, how: str) -> Path:
    if not path.exists():
        raise SystemExit(f"missing {path.relative_to(ROOT)}; regenerate it with: {how}")
    return path


def save(fig: plt.Figure, stem: str) -> list[Path]:
    out = []
    for ext, kw in (
        ("svg", {"metadata": {"Date": None, "Creator": None}}),
        ("png", {"dpi": 600, "metadata": {"Software": None}}),
    ):
        p = FIG_DIR / f"{stem}.{ext}"
        fig.savefig(p, bbox_inches="tight", pad_inches=0.05, **kw)
        out.append(p)
    plt.close(fig)
    return out
