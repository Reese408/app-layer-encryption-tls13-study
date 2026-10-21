# Experiment C (H5): Determinism Check and Pilot Report

| Item | Value |
|---|---|
| Protocol | [research-design.md §7](../docs/research-design.md#7-experiment-c-llm-inference-h5-pre-registered-protocol), pre-registered in commit `fcfdcee` (2026-10-21) before any H5 data |
| Pipeline check | `results/raw/20261021T144808Z-infer-smoke/`: 2 runs, 8 measured requests each (not H5 data) |
| Determinism check | `results/raw/20261021T145025Z-infer-determinism/`: 40 generations |
| Pilot | `results/raw/20261021T145225Z-infer-pilot/`: 6 runs (3 blocks × 2 lengths), 32 measured requests per run, commit `d70823c`, clean tree |
| Model / runtime | `qwen3:8b`, digest `500a1f067a9f…8b41`, Q4_K_M; Ollama 0.35.0 on the host (Metal GPU) |
| Analysis | `analysis/h5.py` → `results/processed/<dir>/h5.md` |

Every number below comes from those files through `analysis/h5.py` or the pandas one-liners recorded in §6. **The pilot is not the H5 test.** Its purpose is to check the pipeline, precision and runtime (gate G7). Its *n* = 3 result is reported here only to show the procedure working.

## 1. Determinism check (§7.3)

- **Required criterion: met.** All 40 generations (4 prompts × 2 lengths × 5 repeats) stopped at the token limit, with `done_reason=length` and `eval_count == num_predict`.
- **Reported: byte-identical output.** For every prompt × length, the 5 repeats produced one SHA-256 of the text. The same held inside every pilot run: the `hash_consistent` column is true in all 6 runs, across both modes.
- **Residency.** `load_duration` was at most 1.31 ms, so the model never reloaded.
- **Drift between repeats.** Repeats several minutes apart varied: the SD of generation time was 28–37 ms on about 620–650 ms (short) and 170–293 ms on about 5.2 s (long). That is roughly 5%. This is the drift that the interleaved design removes from the comparison. It is not noise in the paired ratio (see §2).

## 2. Pilot results

All 6 runs passed acceptance criteria c1–c6.

| length | measure | plain p50 ms | aead p50 ms | Δ ms [95% CI] | ratio [95% CI] | share UCB % | SD log r |
|---|---|---|---|---|---|---|---|
| short (32) | E2E (primary) | 632.1 | 632.0 | −0.048 [−1.147, 1.051] | 0.99993 [0.99823, 1.00164] | 0.109 | 0.00069 |
| short (32) | HTTP | 631.7 | 631.2 | −0.264 [−1.735, 1.207] | 0.99960 [0.99731, 1.00189] | 0.115 | 0.00092 |
| long (256) | E2E (primary) | 5240.5 | 5237.7 | −2.622 [−9.752, 4.509] | 0.99950 [0.99814, 1.00086] | 0.042 | 0.00055 |
| long (256) | HTTP | 5237.9 | 5236.0 | −2.722 [−9.930, 4.486] | 0.99948 [0.99810, 1.00086] | 0.042 | 0.00056 |

**Instrumented share.** Client and server encryption time divided by E2E was 0.076% for short responses and 0.010% for long ones (median per run). The measured envelope time was 0.44–0.58 ms per request.

**Precision (pre-specified pilot rule, §7.5).** The pooled SD of log *r_b* is **0.00062**, well under the 0.007 trigger. The projected UCB margin at *n* = 5 is about 0.06%. **Five blocks are sufficient, and no change to the number of blocks is proposed.**

**Reading the pilot.** The point estimates are slightly *below* 1, meaning aead is faster, at both lengths. The CIs contain 1. This is consistent with an envelope cost (about 0.5 ms) far smaller than the run-to-run noise in generation time.

## 3. Other observations

| Quantity | Plain | AEAD | Source |
|---|---|---|---|
| Request body (bytes) | 1,022–1,023 | 1,450 (+42%) | `req_body_bytes` |
| Response body, short / long (bytes) | 416 / 1,635 | 641 / 2,266 (+54% / +39%) | `resp_body_bytes` |
| API → Ollama hop (`infer` − Ollama `total_duration`) | 5.4–6.3 ms | 5.3–6.6 ms | Server-Timing, response metadata |
| Server time outside `infer`, `dec` and `enc` | 0.09–0.12 ms | 0.08–0.11 ms | Server-Timing |
| Prompt processing (Ollama, about 200 prompt tokens) | 19.8–21.9 ms | same | `prompt_eval_duration` |
| Generation rate | 48.9–53.4 tokens/s | same | `eval_count/eval_duration` |
| GPU "Device Utilization %" (median per run) | 96–98% during runs | | `ioreg`, both modes interleaved |
| llama-server RSS | about 6.0 GiB | | `ps` |

**Observations:**
- **Wire size.** The relative wire overhead is larger than the 33–36% seen in Experiment A. At about 0.4–2 KB, the envelope's fixed fields (`v`, `kid`, nonce, tag) are a larger fraction of the body.
- **Per-token time.** Generation is slower per token for the long responses (49 vs 53 tokens/s), because cost per token grows with context length.

## 4. Issues found, with proposed handling

1. **API CPU per request is not informative in this experiment.**
   - The container health check (`python -c …urlopen…/healthz` every 5 s, inherited from the Dockerfile) costs about 120–145 ms of CPU per check. Each inference request costs about 1–3 ms of API CPU. The health check therefore dominates the `*_api_cpu_ms_per_req` columns (about 38 ms per short request and about 336 ms per long one, in both modes).
   - It is equal in both modes and does not enter any latency measure.
   - **Proposal:** keep it unchanged, for comparability with Experiments A and B. Report API CPU for Experiment C only as the aead − plain difference, with this caveat. The envelope's CPU cost is better shown by the server stage timers (`dec` + `enc` ≈ 0.1–0.2 ms).
   - *Note for Experiments A and B:* the same check ran there. At hundreds to thousands of requests per second it adds well under 1 ms of CPU per second to each container, and it is equal in both modes. This is recorded as a minor limitation, not a reason to rerun anything.
2. **The two-point fixed/per-token model is not physically interpretable as levels.**
   - Its intercept is negative (about −18 ms), because the cost per token rises with context length, so latency is not linear in tokens.
   - The aead − plain **differences** remain valid comparisons, because both modes use the same model: Δfixed = +0.32 ms [−1.66, 2.30] and Δper-token = −0.012 ms [−0.046, 0.023].
   - **Proposal:** in the H5 report, present only the differences, and use the instrumented stage breakdown (§3) for the fixed cost. Do not interpret the intercept.
3. **The k6 `minVersion` option was ignored** in Experiments A and B (found while building `infer.js`). This has been documented and corrected (commit `c690ed3`; results unaffected). `infer.js` pins TLS 1.3 with `tlsVersion`.

## 5. Runtime and resources

- **Pilot:** 752 s (12.5 min) for 6 runs. A long run took 213–216 s and a short run about 30 s, including the restart of both containers.
- **Full H5 projection:** 5 blocks × (one long and one short run of 56 requests each), which is about 5 × (297 + 41) s plus cooldowns, roughly **30 minutes**.
- **Resources:** about 6 GiB of unified memory for the model, outside the Docker VM. The GPU is near saturation during generation. The host's power-source check passed on every run (AC).

## 6. Decisions requested at gate G7

| # | Decision | Proposal |
|---|---|---|
| G7a | Run the full H5 experiment (`make infer`, 5 blocks, 10 runs, about 30 min), unchanged from the pre-registration | Approve |
| G7b | GPU counters (`ioreg`, 1 Hz) during the full run. Modes are interleaved, so any cost falls equally on both. | Keep on |
| G7c | Issues 1–2 in §4 handled as proposed (reporting only; no change to the protocol or the test) | Approve |

Reproduction: `make infer-determinism`; `make infer-pilot`; `make h5 RUN=results/raw/20261021T145225Z-infer-pilot`.
