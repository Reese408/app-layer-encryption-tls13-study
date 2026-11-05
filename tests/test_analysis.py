"""Tests for the analysis helpers and the k6 sample-line extraction."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import stats as sps

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis"))
sys.path.insert(0, str(ROOT / "scripts"))

import stats  # noqa: E402
from run_matrix import SAMPLE_FIELDS, split_k6_log  # noqa: E402
from validate_pipeline import ts_ns  # noqa: E402


def test_holm_matches_textbook_example():
    # Holm (1979) step-down: sorted p * (m - rank), then cumulative max, capped at 1.
    p = [0.01, 0.04, 0.03, 0.005]
    assert stats.holm(p) == pytest.approx([0.03, 0.06, 0.06, 0.02])


def test_holm_counts_untestable_hypotheses_towards_m():
    # A NaN p still counts in the family (as p = 1), so the correction cannot loosen.
    out = stats.holm([0.02, float("nan"), 0.01])
    assert np.isnan(out[1])
    assert out[2] == pytest.approx(0.03) and out[0] == pytest.approx(0.04)


def test_holm_uses_planned_family_size():
    # Two cells tested out of nine planned: multipliers are 9 and 8, not 2 and 1.
    assert stats.holm([0.001, 0.004], m=9) == pytest.approx([0.009, 0.032])
    with pytest.raises(ValueError):
        stats.holm([0.1, 0.2, 0.3], m=2)


def test_sign_flip_minimum_attainable_p():
    # All five differences positive: only the identity and the all-flip are as extreme.
    assert stats.sign_flip_p(np.array([1.0, 2.0, 3.0, 4.0, 5.0])) == pytest.approx(2 / 32)


def test_cliffs_delta_bounds():
    a, b = np.array([3.0, 4.0]), np.array([1.0, 2.0])
    assert stats.cliffs_delta(a, b) == 1.0
    assert stats.cliffs_delta(b, a) == -1.0
    assert stats.cliffs_delta(a, a) == 0.0


def _runs(plain: list[float], aead: list[float]) -> pd.DataFrame:
    rows = []
    for rep, (p, a) in enumerate(zip(plain, aead, strict=True)):
        for mode, v in (("plain", p), ("aead", a)):
            rows.append(
                {
                    "rep": rep,
                    "mode": mode,
                    "size": "1k",
                    "load": "c1",
                    "load_order": 1,
                    "lat_p50_ms": v,
                }
            )
    return pd.DataFrame(rows)


def test_paired_ratio_and_t_test_against_scipy():
    plain = [1.0, 1.1, 0.9, 1.05, 0.95]
    aead = [1.2, 1.3, 1.15, 1.25, 1.18]
    row = stats.paired(_runs(plain, aead), "lat_p50_ms").iloc[0]
    lr = np.log(np.array(aead) / np.array(plain))
    assert row.ratio == pytest.approx(np.exp(lr.mean()))
    assert row.p_t == pytest.approx(sps.ttest_rel(np.log(aead), np.log(plain)).pvalue)
    assert row.ratio_ci95_lo < row.ratio < row.ratio_ci95_hi
    assert row.delta == pytest.approx(np.mean(np.array(aead) - np.array(plain)))
    assert row.blocks_aead_higher == 5
    # Exact Mann-Whitney with complete separation, n = 5 vs 5: 2 / C(10, 5).
    assert row.p_mannwhitney_exact == pytest.approx(2 / 252)


def test_paired_pairs_by_block_not_by_order():
    # Same values, rows shuffled: the result must not change.
    df = _runs([1.0, 2.0, 3.0], [1.5, 2.5, 3.9])
    a = stats.paired(df, "lat_p50_ms").iloc[0]
    b = stats.paired(df.sample(frac=1, random_state=1), "lat_p50_ms").iloc[0]
    assert a.ratio == pytest.approx(b.ratio) and a.p_t == pytest.approx(b.p_t)


def test_split_k6_log_separates_samples():
    fields = ",".join(str(i) for i in range(len(SAMPLE_FIELDS)))
    text = (
        'time="t" level=info msg="negotiated tls_version=tls1.3" source=console\n'
        f'time="t" level=info msg="S,{fields}" source=console\n'
        "[run] measured_reqs=10\n"
    )
    log, rows = split_k6_log(text)
    assert rows == [fields.split(",")]
    assert "S," not in log and "negotiated" in log and "measured_reqs" in log


def test_split_k6_log_rejects_wrong_field_count():
    with pytest.raises(ValueError):
        split_k6_log('time="t" level=info msg="S,1,2,3" source=console\n')


def test_ts_ns_parses_docker_and_python_timestamps():
    docker = ts_ns("2026-10-20T17:26:53.289435381Z")
    python = ts_ns("2026-10-20T17:26:53.289435+00:00")
    assert docker - python == 381
    assert ts_ns("2026-10-20T17:26:53Z") == ts_ns("2026-10-20T17:26:53.000+00:00")


# --- Experiment C (H5) ----------------------------------------------------------------------

THR = 1 / 0.99


def test_threshold_test_matches_hand_calculation():
    r = np.array([1.002, 0.999, 1.004, 1.001, 1.000])
    x = np.log(r)
    # Hand calculation: t_{0.95,4} = 2.131847 (standard table value).
    se = x.std(ddof=1) / np.sqrt(5)
    ucb = np.exp(x.mean() + 2.131847 * se)
    t = stats.threshold_test(x, THR)
    assert t["ucb"] == pytest.approx(ucb, rel=1e-6)
    assert t["ratio"] == pytest.approx(np.exp(x.mean()))
    assert t["verdict"] == "supported" and t["p_one_sided"] < 0.05


@pytest.mark.parametrize(
    "ratios, verdict",
    [
        ([1.001, 1.002, 1.000, 1.001, 1.002], "supported"),
        ([0.99, 1.03, 1.00, 0.98, 1.02], "inconclusive"),  # noise covers the threshold
        ([1.05, 1.06, 1.04, 1.05, 1.07], "rejected"),
    ],
)
def test_threshold_test_verdicts(ratios, verdict):
    t = stats.threshold_test(np.log(ratios), THR)
    assert t["verdict"] == verdict
    # Duality: the one-sided p is below alpha exactly when the UCB is below the threshold.
    assert (t["p_one_sided"] < 0.05) == (t["ucb"] < THR)
    assert t["lcb"] <= t["ratio"] <= t["ucb"]


def test_h5_expected_order_and_overall():
    import h5

    o = h5.expected_order("aead", 8, 16)
    assert o[:4] == [("aead", "p1"), ("plain", "p2"), ("aead", "p3"), ("plain", "p4")]
    assert sum(m == "plain" for m, _ in o) == 8
    res = pd.DataFrame(
        {"role": ["primary", "primary", "secondary"], "verdict": ["supported"] * 2 + ["x"]}
    )
    assert h5.overall(res) == "supported"
    res.loc[1, "verdict"] = "inconclusive"
    assert h5.overall(res) == "inconclusive"
    res.loc[0, "verdict"] = "rejected"
    assert h5.overall(res) == "rejected"


def test_envelope_breakdown_shares_and_residual():
    import envelope_micro as em

    rows = []
    for proc in range(3):
        base = {"proc": proc, "size": "1k", "plaintext_bytes": 1024, "envelope_bytes": 1400}
        for op, ns in {
            "seal.whole": 10_000, "seal.urandom": 500, "seal.aad": 500, "seal.aesgcm": 3000,
            "seal.b64": 2000, "seal.json": 2000, "open.whole": 8000, "open.json": 2000,
            "open.b64": 2000, "open.aesgcm": 2000,
        }.items():  # fmt: skip
            for rep in range(5):
                rows.append({**base, "op": op, "rep": rep, "ns_per_op": ns + proc * 10 * rep})
    b = em.breakdown(pd.DataFrame(rows)).set_index("op")
    assert b.loc["seal.aesgcm", "share_median"] == pytest.approx(0.3, abs=0.01)
    assert b.loc["seal.residual", "share_median"] == pytest.approx(0.2, abs=0.01)
    assert b.loc["open.residual", "us_median"] == pytest.approx(2.0, abs=0.05)
    assert (b.n_proc == 3).all()
