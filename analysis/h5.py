"""Experiment C (H5, LLM inference): run acceptance criteria and the pre-registered analysis.

Protocol: docs/research-design.md §7 (pre-registered 2026-10-21, before any H5 data).

Run level (unit of analysis). For run b at length L, every measured request is in
<run>.samples.csv (complete log). Per mode the run contributes the median E2E (iter_ms) and
the median HTTP latency (req_ms) over its measured requests; r_b = aead / plain.

H5 per length: one-sided 95% upper bound of the geometric-mean ratio over blocks
(stats.threshold_test) against 1 / 0.99. H5 overall: supported only if supported at every
length; rejected if rejected at any; otherwise inconclusive.

Also reported: delta (ms) and ratio with two-sided 95% CIs, SD of log r_b, the same test on
HTTP latency (secondary), the instrumented overhead share (mechanistic, not the test), a
per-block fixed + per-token decomposition, tokens/s, the API->Ollama hop, CPU and memory.

Usage: analysis/h5.py results/raw/<dir>
  -> results/processed/<dir>/{validation.json, h5_runs.csv, h5.csv, h5.md}
"""

from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aggregate import ROOT, cpu_window  # noqa: E402
from stats import t_ci, threshold_test  # noqa: E402

THRESHOLD_SHARE = 0.01
THRESHOLD_RATIO = 1 / (1 - THRESHOLD_SHARE)  # s = 1 - 1/r < 1%  <=>  r < 1/0.99
CYCLE = [(0, "A"), (1, "B"), (2, "A"), (3, "B"), (0, "B"), (1, "A"), (2, "B"), (3, "A")]
API = {"plain": "aead-study-api-plain-1", "aead": "aead-study-api-aead-1"}
MODES = ("plain", "aead")


def _ts_ns(s: str) -> int:
    return int(pd.Timestamp(s).value)


def expected_order(first: str, warmup_n: int, measure_n: int) -> list[tuple[str, str]]:
    second = "aead" if first == "plain" else "plain"
    out = []
    for i in range(warmup_n, warmup_n + measure_n):
        p, slot = CYCLE[i % len(CYCLE)]
        out.append((first if slot == "A" else second, f"p{p + 1}"))
    return out


def load(raw: Path) -> tuple[dict, dict, pd.DataFrame, dict[str, pd.DataFrame]]:
    cfg = tomllib.loads((ROOT / "experiment.toml").read_text())
    man = json.loads((raw / "manifest.json").read_text())
    runs = pd.read_csv(raw / "runs.csv")
    req = {r.run_id: pd.read_csv(raw / f"{r.run_id}.samples.csv") for r in runs.itertuples()}
    return cfg, man, runs, req


def validate(raw: Path) -> dict:
    cfg, man, runs, req = load(raw)
    inf = cfg["inference"]
    prof = man["config"]["profile"]
    res = pd.read_csv(raw / "resources.csv")
    out = []
    for r in runs.itertuples():
        s = req[r.run_id]
        summ = json.loads((raw / f"{r.run_id}.summary.json").read_text())["k6"]["metrics"]
        checks_ok = all(
            summ.get(f"checks{{phase:measure,mode:{m}}}", {}).get("values", {}).get("rate") == 1
            for m in MODES
        )
        t0 = summ_t0(raw, r.run_id)
        start = int((t0 + s.t_ms.min()) * 1e6)
        end = int((t0 + (s.t_ms + s.iter_ms).max()) * 1e6)
        k6_cpu, _ = cpu_window(res, f"aead-study-k6-{r.run_id}", start, end)
        k6_frac = k6_cpu / (cfg["client_cpu_limit"] * 100)
        crit = {
            "c1_no_errors": bool(
                r.k6_exit_code == 0
                and (s.status == 200).all()
                and (s.integrity == 1).all()
                and checks_ok
            ),
            "c2_token_limit": bool(
                (s.eval_count == r.max_tokens).all() and (s.done_reason == "length").all()
            ),
            "c3_resident": bool(
                (s.load_ms < inf["load_duration_max_ms"]).all()
                and all(
                    getattr(r, f"ollama_{w}_loaded")
                    and getattr(r, f"ollama_{w}_size") == getattr(r, f"ollama_{w}_size_vram")
                    for w in ("pre", "post")
                )
            ),
            "c4_version_digest": bool(
                all(
                    getattr(r, f"ollama_{w}_version") == inf["ollama_version"]
                    and getattr(r, f"ollama_{w}_digest") == inf["model_digest"]
                    for w in ("pre", "post")
                )
            ),
            "c5_counts_and_order": bool(
                len(s) == prof["measure_n"]
                and list(zip(s["mode"], s.prompt_id, strict=True))
                == expected_order(r.first, inf["warmup_n"], prof["measure_n"])
            ),
            "c6_client_not_saturated": bool(k6_frac < cfg["client_saturation_threshold"]),
        }
        out.append(
            {
                "run_id": r.run_id,
                **crit,
                "k6_cpu_frac_of_limit": round(float(k6_frac), 4),
                "max_load_ms": float(s.load_ms.max()),
                "pass": all(crit.values()),
            }
        )
    return {"raw": raw.name, "runs": out, "n_pass": sum(x["pass"] for x in out)}


def summ_t0(raw: Path, run_id: str) -> float:
    return json.loads((raw / f"{run_id}.summary.json").read_text())["t0_epoch_ms"]


def per_run(raw: Path, valid: set[str]) -> pd.DataFrame:
    cfg, man, runs, req = load(raw)
    res = pd.read_csv(raw / "resources.csv")
    host_p = raw / "host_resources.csv"
    host = pd.read_csv(host_p) if host_p.exists() else pd.DataFrame()
    rows = []
    for r in runs.itertuples():
        s = req[r.run_id].copy()
        t0 = summ_t0(raw, r.run_id)
        start = int((t0 + s.t_ms.min()) * 1e6)
        end = int((t0 + (s.t_ms + s.iter_ms).max()) * 1e6)
        s["crypto_ms"] = (
            s[["client_enc_ms", "client_dec_ms", "srv_dec_ms", "srv_enc_ms"]].fillna(0).sum(axis=1)
        )
        s["hop_ms"] = s.srv_infer_ms - s.ollama_total_ms
        s["nonmodel_ms"] = s.iter_ms - s.ollama_total_ms  # E2E outside Ollama's own time
        s["srv_other_ms"] = s.srv_app_ms - s.srv_infer_ms - s.srv_dec_ms - s.srv_enc_ms
        s["tok_s"] = s.eval_count / (s.eval_ms / 1e3)
        row = {
            "run_id": r.run_id,
            "rep": r.rep,
            "length": r.length,
            "max_tokens": r.max_tokens,
            "first": r.first,
            "valid": r.run_id in valid,
        }
        n_total = (cfg["inference"]["warmup_n"] + len(s)) / 2  # requests served per container
        for m in MODES:
            g = s[s["mode"] == m]
            row |= {
                f"{m}_e2e_p50": g.iter_ms.median(),
                f"{m}_http_p50": g.req_ms.median(),
                f"{m}_e2e_mean": g.iter_ms.mean(),
                f"{m}_crypto_p50": g.crypto_ms.median(),
                f"{m}_infer_p50": g.srv_infer_ms.median(),
                f"{m}_ollama_total_p50": g.ollama_total_ms.median(),
                f"{m}_prompt_eval_p50": g.prompt_eval_ms.median(),
                f"{m}_eval_p50": g.eval_ms.median(),
                f"{m}_hop_p50": g.hop_ms.median(),
                f"{m}_nonmodel_p50": g.nonmodel_ms.median(),
                f"{m}_srv_other_p50": g.srv_other_ms.median(),
                f"{m}_tok_s_p50": g.tok_s.median(),
                f"{m}_prompt_eval_count_p50": g.prompt_eval_count.median(),
                f"{m}_req_bytes": g.req_body_bytes.median(),
                f"{m}_resp_bytes": g.resp_body_bytes.median(),
            }
            cpu, mem = cpu_window(res, API[m], start, end)
            # Both containers share the measured window; each served exactly this mode's
            # measured requests in it.
            row[f"{m}_api_cpu_ms_per_req"] = cpu / 100 * (end - start) / 1e6 / len(g)
            row[f"{m}_api_mem_mib_max"] = mem
            row[f"{m}_n"] = len(g)
        row["n_served_per_container"] = n_total
        a = s[s["mode"] == "aead"]
        row["instrumented_share_p50"] = (a.crypto_ms / a.iter_ms).median()
        row["hash_consistent"] = bool(s.groupby("prompt_id").resp_sha256_16.nunique().max() == 1)
        if not host.empty:
            h = host[(host.run_id == r.run_id) & (host.kind == "proc")]
            ls = h[h.name == "llama-server"].sort_values("t_ns")
            if len(ls) >= 2:
                row["llama_cpu_s_per_req"] = (ls.cpu_time_s.iloc[-1] - ls.cpu_time_s.iloc[0]) / (
                    2 * n_total
                )
                row["llama_rss_mib_max"] = ls.rss_bytes.max() / 2**20
            gpu = host[(host.run_id == r.run_id) & (host.kind == "gpu")]
            if len(gpu):
                row["gpu_device_util_p50"] = gpu.gpu_device_util_pct.median()
                row["gpu_in_use_mem_gib_max"] = gpu.gpu_in_use_mem_bytes.max() / 2**30
        rows.append(row)
    df = pd.DataFrame(rows)
    for k in ("e2e", "http"):
        df[f"r_{k}"] = df[f"aead_{k}_p50"] / df[f"plain_{k}_p50"]
        df[f"d_{k}_ms"] = df[f"aead_{k}_p50"] - df[f"plain_{k}_p50"]
    df["share_e2e"] = 1 - 1 / df.r_e2e
    # Exploratory (not pre-registered): split each run's E2E difference into the part inside
    # Ollama's reported total_duration and the part outside it (client, TLS, API, envelope,
    # hop). The model cannot see the mode, so the inside part is attributable to model noise.
    df["d_model_ms"] = df.aead_ollama_total_p50 - df.plain_ollama_total_p50
    df["d_nonmodel_ms"] = df.aead_nonmodel_p50 - df.plain_nonmodel_p50
    return df


def h5(df: pd.DataFrame) -> pd.DataFrame:
    v = df[df.valid]
    out = []
    for length in sorted(v.length.unique(), key=lambda x: v[v.length == x].max_tokens.iloc[0]):
        g = v[v.length == length]
        for k, role in (("e2e", "primary"), ("http", "secondary")):
            lr = np.log(g[f"r_{k}"].to_numpy())
            t = threshold_test(lr, THRESHOLD_RATIO)
            lo, hi = t_ci(lr, 0.95)
            dlo, dhi = t_ci(g[f"d_{k}_ms"].to_numpy(), 0.95)
            out.append(
                {
                    "length": length,
                    "max_tokens": int(g.max_tokens.iloc[0]),
                    "measure": k,
                    "role": role,
                    "n_blocks": t["n"],
                    "plain_p50_ms": g[f"plain_{k}_p50"].median(),
                    "aead_p50_ms": g[f"aead_{k}_p50"].median(),
                    "delta_ms": g[f"d_{k}_ms"].mean(),
                    "delta_ci95_lo": dlo,
                    "delta_ci95_hi": dhi,
                    "ratio": t["ratio"],
                    "ratio_ci95_lo": float(np.exp(lo)),
                    "ratio_ci95_hi": float(np.exp(hi)),
                    "share_pct": (1 - 1 / t["ratio"]) * 100,
                    "share_ucb_pct": (1 - 1 / t["ucb"]) * 100,
                    "ucb_ratio": t["ucb"],
                    "lcb_ratio": t["lcb"],
                    "p_one_sided": t["p_one_sided"],
                    "sd_log_r": float(np.std(lr, ddof=1)) if len(lr) > 1 else float("nan"),
                    "verdict": t["verdict"],
                    "instrumented_share_pct": g.instrumented_share_p50.median() * 100,
                }
            )
    return pd.DataFrame(out)


def overall(res: pd.DataFrame) -> str:
    v = res[res.role == "primary"].verdict.tolist()
    if not v:
        return "inconclusive"
    if all(x == "supported" for x in v):
        return "supported"
    if any(x == "rejected" for x in v):
        return "rejected"
    return "inconclusive"


def decomposition(df: pd.DataFrame) -> pd.DataFrame:
    """Per block and mode: E2E = a + b * tokens through the two length levels (fixed vs
    per-token cost); the aead - plain differences in a and b get t CIs over blocks."""
    v = df[df.valid]
    rows = []
    for rep, g in v.groupby("rep"):
        if g.length.nunique() < 2:
            continue
        g = g.sort_values("max_tokens")
        x = g.max_tokens.to_numpy(dtype=float)
        rec = {"rep": rep}
        for m in MODES:
            y = g[f"{m}_e2e_p50"].to_numpy()
            b = (y[-1] - y[0]) / (x[-1] - x[0])
            rec[f"{m}_fixed_ms"], rec[f"{m}_per_token_ms"] = y[0] - b * x[0], b
        rows.append(rec)
    blocks = pd.DataFrame(rows)
    out = []
    for comp in ("fixed_ms", "per_token_ms"):
        if blocks.empty:
            break
        d = (blocks[f"aead_{comp}"] - blocks[f"plain_{comp}"]).to_numpy()
        lo, hi = t_ci(d, 0.95)
        out.append(
            {
                "component": comp,
                "plain_mean": blocks[f"plain_{comp}"].mean(),
                "aead_mean": blocks[f"aead_{comp}"].mean(),
                "delta_mean": d.mean(),
                "delta_ci95_lo": lo,
                "delta_ci95_hi": hi,
                "n_blocks": len(d),
            }
        )
    return pd.DataFrame(out)


def to_markdown(
    raw: Path, val: dict, df: pd.DataFrame, res: pd.DataFrame, dec: pd.DataFrame
) -> str:
    def f(x: float, d: int = 3) -> str:
        return "n/a" if pd.isna(x) else f"{x:.{d}f}"

    lines = [
        f"# Experiment C (H5): `{raw.name}`",
        "",
        f"Valid runs: {val['n_pass']}/{len(val['runs'])}. Threshold: overhead share "
        f"Δ/T_aead < 1% (ratio < {THRESHOLD_RATIO:.6f}); one-sided 95% UCB, df = n − 1.",
        "",
        f"**H5 overall (primary, E2E): {overall(res)}**",
        "",
        "| length | measure | n | plain p50 ms | aead p50 ms | Δ ms [95% CI] | ratio [95% CI] "
        "| share % | share UCB % | p (one-sided) | SD log r | verdict | instrumented share % |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in res.itertuples():
        lines.append(
            f"| {r.length} ({r.max_tokens}) | {r.measure} ({r.role}) | {r.n_blocks} | "
            f"{f(r.plain_p50_ms, 1)} | {f(r.aead_p50_ms, 1)} | {f(r.delta_ms, 3)} "
            f"[{f(r.delta_ci95_lo, 3)}, {f(r.delta_ci95_hi, 3)}] | {f(r.ratio, 5)} "
            f"[{f(r.ratio_ci95_lo, 5)}, {f(r.ratio_ci95_hi, 5)}] | {f(r.share_pct, 3)} | "
            f"{f(r.share_ucb_pct, 3)} | {f(r.p_one_sided, 4)} | {f(r.sd_log_r, 5)} | "
            f"{r.verdict} | {f(r.instrumented_share_pct, 4)} |"
        )
    if not dec.empty:
        lines += [
            "",
            "## Fixed and per-token components (E2E p50, per block, two length levels)",
            "",
            "| component | plain | aead | Δ [95% CI] | n |",
            "|---|---|---|---|---|",
        ]
        for r in dec.itertuples():
            lines.append(
                f"| {r.component} | {f(r.plain_mean, 4)} | {f(r.aead_mean, 4)} | "
                f"{f(r.delta_mean, 4)} [{f(r.delta_ci95_lo, 4)}, {f(r.delta_ci95_hi, 4)}] | "
                f"{r.n_blocks} |"
            )
    cols = [
        "run_id",
        "valid",
        "plain_e2e_p50",
        "aead_e2e_p50",
        "r_e2e",
        "d_e2e_ms",
        "aead_crypto_p50",
        "plain_hop_p50",
        "aead_hop_p50",
        "plain_tok_s_p50",
        "aead_tok_s_p50",
        "plain_api_cpu_ms_per_req",
        "aead_api_cpu_ms_per_req",
        "hash_consistent",
    ]
    lines += ["", "## Runs", "", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in df[[c for c in cols if c in df]].itertuples(index=False):
        lines.append(
            "| " + " | ".join(f(x, 4) if isinstance(x, float) else str(x) for x in r) + " |"
        )
    v = df[df.valid]
    if len(v) > 2:
        lines += [
            "",
            "## Exploratory: where the run-level differences are (not pre-registered)",
            "",
            f"corr(Δ E2E, Δ Ollama total_duration) over runs = "
            f"{np.corrcoef(v.d_e2e_ms, v.d_model_ms)[0, 1]:.3f}",
            "",
            "| length | n | Δ outside model, mean ms [95% CI] | min | max | "
            "instrumented envelope time (aead), median ms |",
            "|---|---|---|---|---|---|",
        ]
        for length, g in v.groupby("length", sort=False):
            lo, hi = t_ci(g.d_nonmodel_ms.to_numpy(), 0.95)
            lines.append(
                f"| {length} | {len(g)} | {f(g.d_nonmodel_ms.mean())} [{f(lo)}, {f(hi)}] | "
                f"{f(g.d_nonmodel_ms.min())} | {f(g.d_nonmodel_ms.max())} | "
                f"{f(g.aead_crypto_p50.median())} |"
            )
        if "gpu_device_util_p50" in v:
            lines += [
                "",
                f"GPU Device Utilization (median per run): {f(v.gpu_device_util_p50.min(), 0)}"
                f"–{f(v.gpu_device_util_p50.max(), 0)}%; llama-server RSS max "
                f"{f(v.llama_rss_mib_max.max(), 0)} MiB; generation rate "
                f"{f(v[['plain_tok_s_p50', 'aead_tok_s_p50']].min().min(), 1)}–"
                f"{f(v[['plain_tok_s_p50', 'aead_tok_s_p50']].max().max(), 1)} tokens/s.",
            ]
    lines += ["", "## Acceptance criteria", ""]
    for r in val["runs"]:
        bad = [k for k, v in r.items() if isinstance(v, bool) and not v and k != "pass"]
        lines.append(f"- `{r['run_id']}`: {'pass' if r['pass'] else 'FAIL ' + ', '.join(bad)}")
    return "\n".join(lines) + "\n"


def main() -> None:
    raw = Path(sys.argv[1]).resolve()
    out = ROOT / "results" / "processed" / raw.name
    out.mkdir(parents=True, exist_ok=True)
    val = validate(raw)
    (out / "validation.json").write_text(json.dumps(val, indent=2))
    df = per_run(raw, {r["run_id"] for r in val["runs"] if r["pass"]})
    df.to_csv(out / "h5_runs.csv", index=False)
    res = h5(df)
    res.to_csv(out / "h5.csv", index=False)
    dec = decomposition(df)
    dec.to_csv(out / "h5_decomposition.csv", index=False)
    md = to_markdown(raw, val, df, res, dec)
    (out / "h5.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
