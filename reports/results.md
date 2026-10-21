# Results: Application-Layer AES-256-GCM over TLS 1.3

| Item | Value |
|---|---|
| Experiment A (closed loop, primary) | `results/raw/20261020T233709Z-full/`: 90 runs, 2026-10-20 23:37 – 2026-10-21 01:33 UTC, commit `49c57b7` |
| Experiment B (open loop, secondary) | `results/raw/20261021T013349Z-openloop/`: 30 runs, 2026-10-21 01:33 – 02:12 UTC, commit `49c57b7` (see §2.3) |
| Analysis | `analysis/aggregate.py`, `analysis/stats.py`, `analysis/validate_pipeline.py`, `analysis/diagnostics.py` |
| Pre-registration | [docs/research-design.md](../docs/research-design.md) §2 (hypotheses) and §5 (statistics), fixed before Experiment A was run; acceptance criteria from [pilot.md §10.3](pilot.md#103-validation-of-the-revised-pipeline) |
| Exclusions | none: 120 of 120 runs pass every acceptance criterion |

Every number in this report is produced by the analysis scripts from `results/raw/`. The commands are listed in §9.

## 1. Summary

Both experiments compare two configurations of one FastAPI service under TLS 1.3 (TLS_AES_256_GCM_SHA384): `plain`, with TLS only, and `aead`, with TLS plus an AES-256-GCM envelope over the request and response bodies. The two run the same image, code path, and deterministic workload.

- **Median latency (H1).** The envelope raised median request latency in all nine closed-loop cells. All nine are significant after Holm correction. The increase was **+13% to +52%**, which is **+0.015 ms** for 1 KiB at concurrency 1 and **+65 ms** for 256 KiB at concurrency 64.
- **Throughput (H3).** Throughput fell in all nine cells (**−16% to −61%**, all significant). The largest relative losses are at concurrency 1, where one k6 virtual user also performs the client-side encryption and decryption.
- **Tail latency (H2).** p99 latency was higher in all 45 block pairs, but only five of the nine cells are significant after Holm correction: both higher-concurrency 16 KiB cells and all three 256 KiB cells. The 1 KiB cells and 16 KiB at concurrency 1 have wide intervals.
- **Payload size (H4).** The absolute latency cost grows with payload size at every concurrency level: 0.16, 1.99, and 8.05 ms per doubling of the payload at 1, 16, and 64 VUs. All three are significant.
- **Server CPU and memory.** The server spends **18–53% more CPU per request** in `aead` mode. Envelope decryption and encryption account for 34–43% of the server's compute time per request. Peak memory did not differ significantly in any closed-loop cell.
- **End-to-end cost.** Including the client's own cryptography, the median end-to-end time grew by **+19% to +160%**. In `aead` mode at concurrency 1, client-side overhead is 42–45% of the end-to-end time.
- **Open loop.** At fixed sub-capacity arrival rates (Experiment B), p99 latency rose in all three sizes (+20% to +49%) and median latency rose at 1 KiB and 256 KiB (+14% and +13%). At 16 KiB, the `plain` configuration had the *higher* median latency, a reproducible effect attributed to processing outside the request handler (§5.3). Its cause has not been established.

## 2. Procedure and data quality

### 2.1 Environment

| Component | Value (from the run manifests) |
|---|---|
| Host | Apple M5 Pro, 18 cores, 64 GiB, macOS 26.6, on AC power for every run |
| Docker VM | 18 vCPU, 7.7 GiB |
| API containers | 1 Uvicorn worker each, limited to 2.0 CPU and 1 GiB; Python 3.12.15, FastAPI 0.153.0, Uvicorn 0.54.0, `cryptography` 50.0.2 |
| Load generator | k6 v2.3.0 in Docker, limited to 8.0 CPU |
| TLS | TLS 1.3 only, TLS_AES_256_GCM_SHA384, verified on every request |

### 2.2 Design

- **Experiment A (closed loop).** 2 modes × 3 payload sizes (1, 16, and 256 KiB) × 3 concurrency levels (1, 16, and 64 constant VUs) × 5 repetitions. The 90 runs were randomized within each repetition block. Each run had a 10 s warm-up, which is discarded, and a 60 s measurement window.
- **Experiment B (open loop).** 2 modes × 3 sizes × 5 repetitions at fixed arrival rates of 7,000, 1,900, and 140 requests/s. The rates are about 50% of the lower configuration's pilot capacity. Each run has a ramped, drained warm-up scenario (10 s) and a 60 s constant-rate measurement scenario.
- **Per-run protocol.** Before every run, both API containers were recreated. The runner waited for the health check and a TLS 1.3 readiness probe, then recorded a 2 s idle baseline.
- **Unit of analysis.** The unit is one run. `aead` and `plain` are paired within each block (*n* = 5 pairs per cell). The effect size is the geometric-mean ratio aead/plain with a paired-*t* 95% CI, together with the mean absolute difference and its CI. The test is a two-sided paired *t*-test on log ratios, with Holm correction within each metric family. The sign-flip and Mann–Whitney tests and Cliff's δ are reported in `stats.md` as robustness checks. With 5 pairs, the exact sign-flip test cannot go below p = 0.0625, so it cannot reach significance on its own (research-design §5).

### 2.3 Acceptance criteria and incidents

| Criterion | Experiment A | Experiment B |
|---|---|---|
| Run health (k6 exit 0, 0 errors, all checks and byte-exact integrity) | 90/90 | 30/30 |
| Restart isolation R1 (a–e) | 90/90 each | 30/30 each |
| Request sample R6 (a–d) | 90/90 each | 30/30 each |
| Open loop: no drops in the measurement window; achieved rate within ±1%; one request per iteration | n/a | 30/30 each |
| Host on AC power; no suspension | 90/90 each | 30/30 each |
| k6 CPU below 85% of its limit | 90/90 (maximum 31.8%) | 30/30 (maximum 24.1%) |

Further data-quality figures:
- **Sample accuracy.** The largest sample-quantile errors against k6's exact values were 0.72% at p50 and 5.42% at p95 in Experiment A, and 1.11% and 3.93% in Experiment B.
- **Open-loop rate and drops.** The achieved rates were 0.99999–1.00012 of target. Warm-up drops were zero in every run.
- **Schedule lag.** The median of the per-run schedule-lag p99 was 1.5 ms. The maximum was 21.1 ms, in `017-plain-256k-a140-r2`, which still delivered its rate exactly and dropped nothing.
- **Restart isolation.** In Experiment A, the per-cell range of peak working set across repetitions was at most 13.1 MiB (median 3.7 MiB), and run order was uncorrelated with peak working set (Spearman ρ = 0.02). In the pilot, without per-run restarts, the corresponding figures were 78.3 MiB, 11.3 MiB, and ρ = 0.28.

Incidents, recorded in full and not hidden:

1. **`results/raw/20261020T233530Z-full/`: aborted.** The first launch of the matrix was stopped after one completed run. The run itself was healthy. The orchestrating shell would have exceeded the agent tool's 2-hour limit for background jobs, so the matrix was restarted as a detached process. The orphaned k6 container of the second run was removed before it finished. The directory is kept unmodified and excluded, and no data from it is used.
2. **The Experiment B manifest records `dirty: true`.** While Experiment A was running, documentation and two new Makefile targets (`grafana`, `grafana-down`) were edited in the working tree, and Experiment B recorded its manifest after those edits. No file on the measurement path was modified: `k6/`, `scripts/`, `experiment.toml`, `compose.yaml`, and `app/` were all unchanged since `49c57b7`. These edits did put a small amount of extra activity on the host during Experiment A, namely file writes and short lint runs, but no containers were started.

## 3. Experiment A: closed loop

Values are medians across the five repetitions. The ratio and its CI are paired over blocks. Δ abs is the mean of the per-block differences, aead − plain.

### 3.1 H1: median latency

| Size | Load | plain | aead | aead/plain [95% CI] | Δ% | Δ abs [95% CI] | blocks aead > plain | p (Holm) |
|---|---|---|---|---|---|---|---|---|
| 1k | c1 | 0.119 | 0.133 | 1.130 [1.076, 1.187] | +13.0% | +0.015 [+0.009, +0.022] | 5/5 | 0.0023 |
| 1k | c16 | 0.697 | 0.834 | 1.189 [1.144, 1.235] | +18.9% | +0.135 [+0.098, +0.171] | 5/5 | 0.0007 |
| 1k | c64 | 3.449 | 4.093 | 1.180 [1.119, 1.244] | +18.0% | +0.618 [+0.446, +0.790] | 5/5 | 0.0019 |
| 16k | c1 | 0.241 | 0.325 | 1.382 [1.312, 1.456] | +38.2% | +0.092 [+0.074, +0.109] | 5/5 | 0.0004 |
| 16k | c16 | 2.686 | 3.343 | 1.262 [1.203, 1.325] | +26.2% | +0.716 [+0.526, +0.905] | 5/5 | 0.0007 |
| 16k | c64 | 11.746 | 15.767 | 1.373 [1.302, 1.448] | +37.3% | +4.424 [+3.538, +5.309] | 5/5 | 0.0004 |
| 256k | c1 | 2.475 | 3.711 | 1.523 [1.436, 1.616] | +52.3% | +1.288 [+1.083, +1.494] | 5/5 | 0.0003 |
| 256k | c16 | 36.545 | 53.272 | 1.433 [1.391, 1.477] | +43.3% | +16.052 [+14.768, +17.335] | 5/5 | < 0.0001 |
| 256k | c64 | 152.093 | 215.365 | 1.432 [1.361, 1.506] | +43.2% | +65.000 [+57.418, +72.583] | 5/5 | 0.0003 |

H₀ is rejected in all nine cells (Holm, α = 0.05). The relative cost rises with payload size: about 13–19% at 1 KiB, 26–38% at 16 KiB, and 43–52% at 256 KiB.

### 3.2 H2: tail latency

p99, the primary H2 family:

| Size | Load | plain | aead | aead/plain [95% CI] | Δ% | Δ abs [95% CI] | blocks aead > plain | p (Holm) |
|---|---|---|---|---|---|---|---|---|
| 1k | c1 | 0.155 | 0.247 | 1.269 [0.996, 1.615] | +26.9% | +0.048 [-0.003, +0.099] | 5/5 | 0.0727 |
| 1k | c16 | 1.544 | 2.244 | 1.399 [1.108, 1.766] | +39.9% | +0.737 [+0.014, +1.461] | 5/5 | 0.0605 |
| 1k | c64 | 7.890 | 8.506 | 1.149 [1.045, 1.262] | +14.9% | +1.078 [+0.417, +1.739] | 5/5 | 0.0605 |
| 16k | c1 | 0.336 | 0.487 | 1.553 [1.047, 2.304] | +55.3% | +0.218 [-0.029, +0.465] | 5/5 | 0.0727 |
| 16k | c16 | 5.524 | 7.280 | 1.356 [1.199, 1.532] | +35.6% | +2.066 [+1.018, +3.114] | 5/5 | 0.0139 |
| 16k | c64 | 23.439 | 31.948 | 1.396 [1.274, 1.531] | +39.6% | +9.502 [+6.485, +12.519] | 5/5 | 0.0044 |
| 256k | c1 | 3.290 | 4.738 | 1.474 [1.427, 1.522] | +47.4% | +1.491 [+1.310, +1.672] | 5/5 | < 0.0001 |
| 256k | c16 | 45.328 | 55.455 | 1.381 [1.221, 1.561] | +38.1% | +17.269 [+9.006, +25.532] | 5/5 | 0.0132 |
| 256k | c64 | 184.532 | 227.972 | 1.340 [1.184, 1.516] | +34.0% | +58.626 [+35.886, +81.366] | 5/5 | 0.0139 |

p95, secondary:

| Size | Load | plain | aead | aead/plain [95% CI] | Δ% | Δ abs [95% CI] | blocks aead > plain | p (Holm) |
|---|---|---|---|---|---|---|---|---|
| 1k | c1 | 0.132 | 0.158 | 1.162 [1.074, 1.258] | +16.2% | +0.022 [+0.011, +0.034] | 5/5 | 0.0184 |
| 1k | c16 | 1.435 | 1.821 | 1.275 [1.141, 1.424] | +27.5% | +0.416 [+0.175, +0.657] | 5/5 | 0.0184 |
| 1k | c64 | 5.388 | 5.304 | 1.213 [0.969, 1.518] | +21.3% | +0.974 [-0.185, +2.133] | 4/5 | 0.0750 |
| 16k | c1 | 0.269 | 0.377 | 1.474 [1.222, 1.778] | +47.4% | +0.134 [+0.056, +0.212] | 5/5 | 0.0184 |
| 16k | c16 | 5.376 | 6.986 | 1.326 [1.246, 1.411] | +32.6% | +1.778 [+1.280, +2.275] | 5/5 | 0.0016 |
| 16k | c64 | 13.793 | 17.009 | 1.409 [1.093, 1.817] | +40.9% | +5.910 [+0.129, +11.691] | 5/5 | 0.0401 |
| 256k | c1 | 2.848 | 4.444 | 1.502 [1.428, 1.580] | +50.2% | +1.417 [+1.213, +1.622] | 5/5 | 0.0002 |
| 256k | c16 | 40.635 | 54.689 | 1.420 [1.333, 1.512] | +42.0% | +16.976 [+13.164, +20.788] | 5/5 | 0.0008 |
| 256k | c64 | 171.682 | 223.280 | 1.382 [1.274, 1.498] | +38.2% | +62.082 [+48.704, +75.459] | 5/5 | 0.0023 |

- **Result.** At p99, H₀ is rejected in five of nine cells. In the other four (1 KiB at all concurrencies, and 16 KiB at 1 VU), `aead` was higher in every block, but p99 varies between runs, and with *n* = 5 the intervals are too wide for significance after Holm correction.
- **What it does not show.** The non-significant cells are not evidence of no tail effect.
- **p95.** At p95, eight of nine cells are significant. The exception is 1 KiB at 64 VUs, where `aead` was higher in four of five blocks.

### 3.3 H3: throughput

| Size | Load | plain | aead | aead/plain [95% CI] | Δ% | Δ abs [95% CI] | blocks aead > plain | p (Holm) |
|---|---|---|---|---|---|---|---|---|
| 1k | c1 | 6091 | 4193 | 0.697 [0.663, 0.732] | -30.3% | -1811 [-2066, -1556] | 0/5 | 0.0002 |
| 1k | c16 | 18878 | 14310 | 0.769 [0.716, 0.825] | -23.1% | -4217 [-5015, -3419] | 0/5 | 0.0010 |
| 1k | c64 | 16328 | 14441 | 0.843 [0.789, 0.900] | -15.7% | -2722 [-3942, -1501] | 0/5 | 0.0019 |
| 16k | c1 | 3404 | 1623 | 0.468 [0.438, 0.501] | -53.2% | -1804 [-1946, -1662] | 0/5 | < 0.0001 |
| 16k | c16 | 5456 | 3846 | 0.699 [0.661, 0.738] | -30.1% | -1609 [-1785, -1434] | 0/5 | 0.0002 |
| 16k | c64 | 5226 | 3838 | 0.714 [0.667, 0.765] | -28.6% | -1483 [-1745, -1220] | 0/5 | 0.0005 |
| 256k | c1 | 379 | 148 | 0.390 [0.373, 0.408] | -61.0% | -233 [-254, -211] | 0/5 | < 0.0001 |
| 256k | c16 | 431 | 287 | 0.667 [0.644, 0.691] | -33.3% | -142 [-157, -127] | 0/5 | < 0.0001 |
| 256k | c64 | 414 | 293 | 0.694 [0.658, 0.731] | -30.6% | -129 [-152, -107] | 0/5 | 0.0002 |

H₀ is rejected in all nine cells. The relative loss is greatest at concurrency 1 (−30%, −53%, and −61%). At concurrency 1, a single virtual user performs client-side WebCrypto encryption and decryption between requests, so the client's own cost reduces the request rate. At 16 and 64 VUs, where the client has headroom, the loss is −16% to −33%, which reflects the extra server work.

### 3.4 H4: dependence on payload size

| Load | Blocks | Slope of Δp50 on log₂(bytes) (ms per doubling) | 95% CI | p | p (Holm) |
|---|---|---|---|---|---|
| c1 | 5 | 0.159 | [0.134, 0.185] | 0.00007 | 0.00007 |
| c16 | 5 | 1.990 | [1.830, 2.150] | 0.000004 | 0.00001 |
| c64 | 5 | 8.048 | [7.114, 8.982] | 0.00002 | 0.00004 |

H₀ is rejected at every concurrency level: the absolute median-latency cost grows with payload size. The model is linear in log₂(size) over only three sizes (1, 16, and 256 KiB), so the slope summarizes the trend and is not a validated functional form.

### 3.5 Secondary metrics

End-to-end median, which includes client-side encryption, decryption, and integrity checking (k6 `iteration_duration`):

| Size | Load | plain | aead | aead/plain [95% CI] | Δ% | Δ abs [95% CI] | blocks aead > plain | p (Holm) |
|---|---|---|---|---|---|---|---|---|
| 1k | c1 | 0.161 | 0.229 | 1.423 [1.364, 1.484] | +42.3% | +0.068 [+0.060, +0.077] | 5/5 | 0.0001 |
| 1k | c16 | 0.735 | 0.930 | 1.256 [1.207, 1.307] | +25.6% | +0.192 [+0.148, +0.236] | 5/5 | 0.0003 |
| 1k | c64 | 3.494 | 4.197 | 1.194 [1.133, 1.259] | +19.4% | +0.677 [+0.504, +0.850] | 5/5 | 0.0007 |
| 16k | c1 | 0.288 | 0.593 | 2.101 [2.000, 2.207] | +110.1% | +0.314 [+0.285, +0.344] | 5/5 | < 0.0001 |
| 16k | c16 | 2.730 | 3.615 | 1.344 [1.278, 1.413] | +34.4% | +0.954 [+0.737, +1.172] | 5/5 | 0.0003 |
| 16k | c64 | 11.797 | 16.050 | 1.392 [1.320, 1.468] | +39.2% | +4.663 [+3.763, +5.563] | 5/5 | 0.0003 |
| 256k | c1 | 2.561 | 6.626 | 2.603 [2.473, 2.740] | +160.3% | +4.103 [+3.779, +4.428] | 5/5 | < 0.0001 |
| 256k | c16 | 36.632 | 55.736 | 1.499 [1.455, 1.544] | +49.9% | +18.524 [+17.300, +19.747] | 5/5 | < 0.0001 |
| 256k | c64 | 152.200 | 217.934 | 1.448 [1.377, 1.523] | +44.8% | +67.461 [+59.992, +74.931] | 5/5 | 0.0002 |

API CPU time per completed request:

| Size | Load | plain | aead | aead/plain [95% CI] | Δ% | Δ abs [95% CI] | blocks aead > plain | p (Holm) |
|---|---|---|---|---|---|---|---|---|
| 1k | c1 | 0.092 | 0.109 | 1.184 [1.124, 1.247] | +18.4% | +0.017 [+0.012, +0.023] | 5/5 | 0.0017 |
| 1k | c16 | 0.054 | 0.072 | 1.303 [1.211, 1.401] | +30.3% | +0.017 [+0.011, +0.023] | 5/5 | 0.0017 |
| 1k | c64 | 0.063 | 0.071 | 1.186 [1.110, 1.268] | +18.6% | +0.011 [+0.007, +0.015] | 5/5 | 0.0020 |
| 16k | c1 | 0.216 | 0.309 | 1.458 [1.360, 1.563] | +45.8% | +0.100 [+0.077, +0.122] | 5/5 | 0.0006 |
| 16k | c16 | 0.187 | 0.266 | 1.432 [1.353, 1.515] | +43.2% | +0.083 [+0.065, +0.101] | 5/5 | 0.0004 |
| 16k | c64 | 0.196 | 0.267 | 1.401 [1.305, 1.504] | +40.1% | +0.079 [+0.059, +0.099] | 5/5 | 0.0008 |
| 256k | c1 | 2.542 | 3.856 | 1.528 [1.442, 1.618] | +52.8% | +1.332 [+1.124, +1.541] | 5/5 | 0.0003 |
| 256k | c16 | 2.371 | 3.560 | 1.497 [1.444, 1.552] | +49.7% | +1.194 [+1.081, +1.306] | 5/5 | < 0.0001 |
| 256k | c64 | 2.467 | 3.480 | 1.441 [1.366, 1.520] | +44.1% | +1.066 [+0.941, +1.191] | 5/5 | 0.0003 |

API peak working set:

| Size | Load | plain | aead | aead/plain [95% CI] | Δ% | Δ abs [95% CI] | blocks aead > plain | p (Holm) |
|---|---|---|---|---|---|---|---|---|
| 1k | c1 | 53.9 | 53.8 | 1.008 [0.945, 1.074] | +0.8% | +0.4 [-2.9, +3.7] | 4/5 | 1.0000 |
| 1k | c16 | 54.5 | 53.9 | 0.991 [0.945, 1.039] | -0.9% | -0.5 [-3.0, +2.1] | 1/5 | 1.0000 |
| 1k | c64 | 58.1 | 59.2 | 1.056 [0.938, 1.189] | +5.6% | +3.4 [-4.1, +11.0] | 3/5 | 1.0000 |
| 16k | c1 | 54.1 | 53.4 | 0.982 [0.948, 1.017] | -1.8% | -1.0 [-2.9, +0.9] | 2/5 | 1.0000 |
| 16k | c16 | 55.7 | 55.7 | 0.994 [0.947, 1.044] | -0.6% | -0.3 [-3.0, +2.4] | 2/5 | 1.0000 |
| 16k | c64 | 63.7 | 66.3 | 1.027 [0.978, 1.078] | +2.7% | +1.7 [-1.5, +4.9] | 4/5 | 1.0000 |
| 256k | c1 | 54.6 | 54.4 | 0.994 [0.958, 1.031] | -0.6% | -0.3 [-2.3, +1.6] | 3/5 | 1.0000 |
| 256k | c16 | 76.0 | 77.6 | 1.023 [0.996, 1.050] | +2.3% | +1.7 [-0.3, +3.8] | 5/5 | 0.6128 |
| 256k | c64 | 143.6 | 147.9 | 1.038 [1.013, 1.063] | +3.8% | +5.5 [+1.9, +9.0] | 5/5 | 0.1126 |

- **CPU.** CPU per request rose significantly in every cell, from +0.011 to +1.33 CPU-ms.
- **Memory.** Peak working set showed no significant difference in any cell. The ratios lie between 0.98 and 1.06, and every CI includes 1 except at 256 KiB / 64 VUs. There the CI is [1.013, 1.063], and the effect is not significant after Holm correction (p = 0.11).

## 4. Where the cost comes from

### 4.1 Server stages

The server reports stage timings in the `Server-Timing` header. The table gives medians across repetitions of the per-run means, from `comparison.md`:

| Size | Load | decrypt (ms) | workload (ms) | encrypt (ms) | crypto share of server compute | Δ API CPU-ms/request |
|---|---|---|---|---|---|---|
| 1k | c1 | 0.0071 | 0.0179 | 0.0061 | 42.5% | 0.0174 |
| 1k | c64 | 0.0049 | 0.0161 | 0.0052 | 38.5% | 0.0082 |
| 16k | c1 | 0.0356 | 0.1267 | 0.0328 | 35.1% | 0.0931 |
| 16k | c64 | 0.0319 | 0.1280 | 0.0330 | 33.7% | 0.0708 |
| 256k | c1 | 0.5294 | 2.0756 | 0.6675 | 36.6% | 1.3146 |
| 256k | c64 | 0.4885 | 1.9800 | 0.5762 | 35.0% | 1.0121 |

- **The envelope steps.** The `decrypt` and `encrypt` stages include envelope parsing, Base64 decoding and encoding, and AES-GCM. Together they take about a third of the server's per-request compute at every size.
- **Agreement with the CPU measurement.** Their sum is close to the measured increase in API CPU per request. At 256 KiB and 1 VU, for example, it is 1.20 ms against 1.31 ms. Two independent instruments therefore agree on the size of the server-side cost.
- **The workload stage.** The workload stage is the same in both modes, as designed: 2.08 against 2.08 ms at 256 KiB and 1 VU, from the per-run `runs.csv`.
- **Definition of `app` time.** The `Server-Timing` `app` value runs from handler entry to the response. It includes reading the request body. At 256 KiB under concurrency, that includes waiting on the single event loop (for example, 111 ms at 64 VUs, against 3.0 ms of compute), so it should not be read as compute time.

### 4.2 Client side and wire size

- **Wire size.** The envelope Base64-encodes the ciphertext, which adds 33–36% to the bytes in each direction. At 16 KiB, for example, a request grows from 16,564 to 22,133 B and a response from 16,749 to 22,342 B.
- **Client microbenchmark.** In the k6 client, WebCrypto seal and open took 0.025 / 0.017 ms per operation for 1 KiB, 0.153 / 0.061 ms for 16 KiB, and 1.88 / 0.79 ms for 256 KiB. This is a single-VU microbenchmark timed with `Date.now()` over N operations (`client_crypto_bench.json`).
- **Share of end-to-end time.** At concurrency 1, client-side overhead is 42–45% of the `aead` end-to-end time. At 64 VUs it is 1.2–2.5%, because queueing for the single server worker dominates there.

## 5. Experiment B: open loop (fixed arrival rate)

This experiment is analysed separately and is never pooled with Experiment A. Throughput is fixed by design and is not tested.

### 5.1 Latency

Median (H1):

| Size | Load | plain | aead | aead/plain [95% CI] | Δ% | Δ abs [95% CI] | blocks aead > plain | p (Holm) |
|---|---|---|---|---|---|---|---|---|
| 1k | a7000 | 0.142 | 0.162 | 1.141 [1.132, 1.149] | +14.1% | +0.020 [+0.019, +0.021] | 5/5 | < 0.0001 |
| 16k | a1900 | 0.442 | 0.343 | 0.775 [0.769, 0.781] | -22.5% | -0.100 [-0.103, -0.097] | 0/5 | < 0.0001 |
| 256k | a140 | 3.138 | 3.552 | 1.126 [1.103, 1.150] | +12.6% | +0.396 [+0.323, +0.469] | 5/5 | < 0.0001 |

p99 (H2):

| Size | Load | plain | aead | aead/plain [95% CI] | Δ% | Δ abs [95% CI] | blocks aead > plain | p (Holm) |
|---|---|---|---|---|---|---|---|---|
| 1k | a7000 | 0.595 | 0.817 | 1.376 [1.311, 1.444] | +37.6% | +0.225 [+0.188, +0.262] | 5/5 | 0.0001 |
| 16k | a1900 | 0.753 | 1.120 | 1.487 [1.463, 1.511] | +48.7% | +0.366 [+0.353, +0.379] | 5/5 | < 0.0001 |
| 256k | a140 | 3.452 | 4.156 | 1.195 [1.151, 1.240] | +19.5% | +0.676 [+0.539, +0.813] | 5/5 | 0.0002 |

p95, secondary:

| Size | Load | plain | aead | aead/plain [95% CI] | Δ% | Δ abs [95% CI] | blocks aead > plain | p (Holm) |
|---|---|---|---|---|---|---|---|---|
| 1k | a7000 | 0.245 | 0.441 | 1.779 [1.677, 1.888] | +77.9% | +0.193 [+0.175, +0.212] | 5/5 | < 0.0001 |
| 16k | a1900 | 0.667 | 0.603 | 0.904 [0.887, 0.921] | -9.6% | -0.064 [-0.075, -0.053] | 0/5 | 0.0001 |
| 256k | a140 | 3.275 | 3.820 | 1.160 [1.136, 1.184] | +16.0% | +0.523 [+0.449, +0.598] | 5/5 | < 0.0001 |

### 5.2 Secondary metrics

End-to-end median:

| Size | Load | plain | aead | aead/plain [95% CI] | Δ% | Δ abs [95% CI] | blocks aead > plain | p (Holm) |
|---|---|---|---|---|---|---|---|---|
| 1k | a7000 | 0.199 | 0.287 | 1.444 [1.431, 1.457] | +44.4% | +0.088 [+0.087, +0.090] | 5/5 | < 0.0001 |
| 16k | a1900 | 0.504 | 0.624 | 1.238 [1.229, 1.247] | +23.8% | +0.120 [+0.116, +0.125] | 5/5 | < 0.0001 |
| 256k | a140 | 3.270 | 6.132 | 1.884 [1.858, 1.910] | +88.4% | +2.887 [+2.806, +2.968] | 5/5 | < 0.0001 |

API CPU per request:

| Size | Load | plain | aead | aead/plain [95% CI] | Δ% | Δ abs [95% CI] | blocks aead > plain | p (Holm) |
|---|---|---|---|---|---|---|---|---|
| 1k | a7000 | 0.094 | 0.104 | 1.108 [1.104, 1.112] | +10.8% | +0.010 [+0.010, +0.011] | 5/5 | < 0.0001 |
| 16k | a1900 | 0.228 | 0.322 | 1.411 [1.399, 1.424] | +41.1% | +0.094 [+0.091, +0.096] | 5/5 | < 0.0001 |
| 256k | a140 | 3.214 | 3.662 | 1.128 [1.100, 1.157] | +12.8% | +0.412 [+0.321, +0.502] | 5/5 | 0.0002 |

API peak working set:

| Size | Load | plain | aead | aead/plain [95% CI] | Δ% | Δ abs [95% CI] | blocks aead > plain | p (Holm) |
|---|---|---|---|---|---|---|---|---|
| 1k | a7000 | 135.4 | 99.7 | 0.872 [0.697, 1.090] | -12.8% | -16.9 [-43.4, +9.7] | 2/5 | 0.3273 |
| 16k | a1900 | 80.1 | 85.4 | 1.038 [0.796, 1.354] | +3.8% | +2.8 [-22.8, +28.3] | 4/5 | 0.7160 |
| 256k | a140 | 108.3 | 124.8 | 1.148 [1.112, 1.186] | +14.8% | +16.1 [+12.3, +19.9] | 5/5 | 0.0008 |

### 5.3 The 16 KiB open-loop result

**The finding.** At 16 KiB and 1,900 requests/s, `plain` had the higher median latency (0.442 against 0.343 ms; `plain` higher in 5 of 5 blocks) and the higher p95. Yet `aead` had the higher p99, the higher end-to-end median, and 41% more CPU per request. The effect is reproducible: the fourth open-loop validation showed the same ordering (`plain` 0.372–0.446 ms against `aead` 0.347–0.364 ms; [pilot.md §10.3](pilot.md#103-validation-of-the-revised-pipeline)).

**Where the time goes.** The per-request sample (medians, from `diagnostics.py`) locates the extra time. In the time from the end of sending the request to the first response byte, minus the server's `app` time, `plain` and `aead` are close everywhere except here:

| Cell | outside-handler time, plain (ms) | outside-handler time, aead (ms) | server compute, plain (ms) | server compute, aead (ms) |
|---|---|---|---|---|
| A: 16k, c1 | 0.078 | 0.090 | 0.127 | 0.196 |
| B: 1k, a7000 | 0.103 | 0.106 | 0.018 | 0.028 |
| B: 16k, a1900 | **0.259** | 0.099 | 0.134 | 0.193 |
| B: 256k, a140 | 0.042 | 0.024 | 2.640 | 3.021 |

**What is established.** Inside the handler, `aead` is slower as expected. The anomaly is about 0.16 ms of extra time for `plain` outside the handler, which covers the network, TLS record processing, HTTP parsing, and event-loop scheduling. It appears only for 16 KiB at this arrival pattern.

**A candidate explanation, untested.** The `plain` 16 KiB request and response bodies exceed the TLS 1.3 maximum record payload of 16,384 bytes (RFC 8446 §5.1) by only 180 and 365 bytes. Each message therefore ends in a very small trailing record. The `aead` bodies end in records of about 5.7 KiB. Combined with connections that sit idle between uses (the open-loop pool rotates among 190 VUs), this record and segment boundary is a plausible transport-level cause. This study does not test it.

**How to settle it.** A dedicated follow-up would vary the `plain` payload around the record boundary (for example, 15.5 KiB against 16.5 KiB) or capture packets. It would need owner approval. Until then, the 16 KiB open-loop median should be read as "not attributable to the envelope", not as evidence that the envelope reduces latency.

## 6. Interpretation

1. **Is the overhead real?** Yes. In the closed-loop experiment, the envelope increased median latency in all nine cells, reduced throughput in all nine, and increased server CPU per request in all nine. Every effect points the same way and is large relative to its CI.
2. **How large is it?** For small payloads the absolute cost is small: about 15 µs on the median at 1 KiB and 1 VU, and about 0.1 ms at 16 KiB. It grows with payload size, reaching about 1.3 ms at 256 KiB and 1 VU, and it is multiplied by queueing at high concurrency (+65 ms at 256 KiB and 64 VUs). In relative terms the median increase is 13–52%.
3. **What drives it?** The envelope steps (Base64 plus AES-GCM plus parsing) account for about a third of the server's compute. On the client, WebCrypto adds a comparable cost. Wire size grows by a third. AES-GCM itself is only part of these costs: the Base64 transport encoding and the JSON envelope are part of the measured design, and this study does not separate them from the cipher. The expectation recorded before data collection (research-design §2), that most of the overhead comes from envelope handling and not from the hardware-accelerated cipher, is therefore consistent with these results but was not tested.
4. **Tails.** The direction is consistent, but significance after correction holds only for 16 KiB at higher concurrency and for 256 KiB. More repetitions would be needed to bound the p99 effect for small payloads.
5. **Open loop.** At a fixed, sub-capacity load, the envelope raises p99 latency and CPU for every size and the median for 1 KiB and 256 KiB. The 16 KiB median is confounded by a transport-level effect that favours `aead` (§5.3).

## 7. Threats to validity and limitations

- **One host.** Everything ran on a single laptop, with client and server sharing one Docker VM. Absolute values do not transfer to servers or to networked deployments. With real network latency, the relative overhead would be smaller.
- **One worker.** One Uvicorn worker per configuration means that queueing at 16 and 64 VUs amplifies the per-request cost (Little's law). The closed-loop high-concurrency cells measure capacity under saturation, not per-request cost.
- **Power, *n*, and intervals.** With *n* = 5 pairs per cell, the paired *t*-test assumes approximately normal log ratios, and the non-parametric checks have a p-value floor of 0.0625. The tail-latency families have low power.
- **Design scope.** The envelope design (Base64-in-JSON, per-request random nonce, keys per direction) is one point in the design space. A binary envelope would change the wire and parsing costs.
- **Client-bound cells at concurrency 1.** At 1 VU, a single VU also does the client crypto, so throughput at c1 reflects both client and server cost. The client never exceeded 32% of its CPU limit, but at c1 one VU is serial by construction.
- **Not measured.** Cold processes and long-lived processes are not measured, because the containers are recreated per run (R1). Key management, rotation, and multi-tenant key lookup are outside scope.
- **Unexplained anomaly.** The 16 KiB open-loop anomaly (§5.3) is reported but not explained.
- **Concurrent documentation work.** Small amounts of documentation and code editing took place on the host during Experiment A (§2.3). Randomized, interleaved block ordering spreads any such disturbance across both modes.

## 8. Dashboards

- **Static dashboard.** `make dashboard RUNS="results/raw/20261020T233709Z-full results/raw/20261021T013349Z-openloop"` writes `results/processed/dashboard/index.html`, an offline page with forest plots, per-run dot plots, the server breakdown, and sample ECDFs.
- **Grafana.** `make grafana RUNS="…"` backfills Prometheus from the same files and serves Grafana at http://127.0.0.1:3000. It has two dashboards: *Results*, showing effect sizes, CIs, Holm p-values, per-cell medians, acceptance criteria, and per-run values, and *Benchmark session timeline*, showing run phases, CPU and memory of the API and k6, and sampled latency over time. It is post hoc and was never running during a measurement ([architecture.md §8](../docs/architecture.md#8-post-hoc-observability-prometheus-and-grafana)).

## 9. Reproduction

```sh
make check-run RUN=results/raw/20261020T233709Z-full      # acceptance criteria
make check-run RUN=results/raw/20261021T013349Z-openloop
uv run python analysis/aggregate.py results/raw/20261020T233709Z-full
uv run python analysis/aggregate.py results/raw/20261021T013349Z-openloop
make stats RUN=results/raw/20261020T233709Z-full          # stats.csv, stats.md, stats_size_trend.csv
make stats RUN=results/raw/20261021T013349Z-openloop
uv run python analysis/diagnostics.py results/raw/20261020T233709Z-full results/raw/20261021T013349Z-openloop results/raw/20261020T154305Z-pilot
```

The complete statistics, including the robustness tests, are in `results/processed/<dir>/stats.md`. The tables in this report are generated from `stats.csv`.
