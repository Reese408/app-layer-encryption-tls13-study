# Results: LLM Inference (Experiment C, H5) and Envelope Cost Breakdown (Experiment D, A1)

| Item | Value |
|---|---|
| Experiment C (H5) | `results/raw/20261021T155308Z-infer/`: 10 runs, 2026-10-21 15:53–16:23 UTC, commit `24bf4fd`, clean tree |
| Experiment C pilot and checks | `20261021T145225Z-infer-pilot/`, `20261021T145025Z-infer-determinism/`, `20261021T144808Z-infer-smoke/`; see [infer-pilot.md](infer-pilot.md) |
| Experiment D (A1) | `results/raw/20261021T162323Z-envelope-micro/`: 5 processes × 25 repetitions, commit `f8d0d9c`, clean tree |
| Pre-registration | [research-design.md §7](../docs/research-design.md#7-experiment-c-llm-inference-h5-pre-registered-protocol) (H5, commit `fcfdcee`) and §8 (A1, commit `24bf4fd`), both committed before the respective data |
| Analysis | `analysis/h5.py`, `analysis/envelope_micro.py` → `results/processed/<dir>/` |
| Exclusions | none: 10 of 10 H5 runs pass acceptance criteria c1–c6 |

Every number in this report is produced by those scripts from `results/raw/`. Commands are listed in §6. The earlier report, [results.md](results.md) (Experiments A and B), is unchanged.

## 1. Summary

- **H5 is supported at both response lengths.** In this local setup, the AES-256-GCM envelope's share of end-to-end latency is below 1% with 95% confidence:
  - **32 tokens:** at most 0.36% (point estimate −0.46%);
  - **256 tokens:** at most 0.65% (point estimate +0.09%).
  - The secondary measure (HTTP latency) gives the same verdict.
- **The run-level differences come from the model, not the envelope.** Across runs, the aead − plain difference in end-to-end time correlates at 0.997 with the difference in Ollama's own reported processing time. The model cannot see which mode a request came from.
  - The time *outside* the model differed by +0.29 ms (32 tokens) and +0.75 ms (256 tokens) on average. Directly timed envelope work was 0.45–0.53 ms per request.
  - This decomposition is exploratory (§2.3).
- **The cipher is a small part of the envelope cost (Experiment D).** On the server, AES-GCM itself accounts for 7–13% of sealing and opening time. Base64 and JSON handling account for 78–94%.
  - This matches the expectation recorded before any data was collected (research-design §2).
  - It means the overhead measured in Experiments A and C belongs to *this* Base64-in-JSON envelope, not to AES-GCM as such.

## 2. Experiment C: H5

### 2.1 Design (as pre-registered)

- **Model:** `qwen3:8b` (Q4_K_M, digest `500a1f067a9f…8b41`) on Ollama 0.35.0, natively on the host GPU. Settings: `temperature=0`, `seed=42`, `think=false`, `num_ctx=4096`, non-streaming.
- **Prompts:** four fixed prompts of about 1 KiB each, at two response lengths (32 and 256 tokens).
- **Load:** one k6 VU alternates between `api-plain` and `api-aead` in a counterbalanced 8-request cycle.
- **Per run:** 8 warm-up and 48 measured requests (24 per mode).
- **Blocks:** 5 blocks, each with one run per length. The first mode alternates by block.
- **Statistics:**
  - per run, *r* = median E2E(aead) / median E2E(plain);
  - per length, the one-sided 95% upper bound of the geometric-mean *r* over blocks;
  - H5 is supported at a length if the bound is < 1/0.99, and overall only if it is supported at both lengths.

### 2.2 Result

| Length | Measure | Plain p50 ms | AEAD p50 ms | Δ ms [95% CI] | Ratio [95% CI] | Share UCB | p (one-sided) | SD log *r* | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| 32 tokens | **E2E (primary)** | 666.6 | 667.4 | −2.99 [−9.87, 3.89] | 0.9954 [0.9849, 1.0061] | **0.358%** | 0.0094 | 0.0086 | **supported** |
| 32 tokens | HTTP | 666.2 | 666.6 | −3.32 [−10.06, 3.42] | 0.9949 [0.9846, 1.0054] | 0.293% | 0.0079 | 0.0084 | supported |
| 256 tokens | **E2E (primary)** | 5347.5 | 5370.9 | +4.84 [−34.09, 43.76] | 1.0009 [0.9937, 1.0082] | **0.645%** | 0.0123 | 0.0058 | **supported** |
| 256 tokens | HTTP | 5346.3 | 5367.9 | +4.08 [−35.25, 43.41] | 1.0008 [0.9935, 1.0081] | 0.637% | 0.0121 | 0.0059 | supported |

The p50 columns are the median, over runs, of each run's median. Δ is the mean of the per-run differences.

**H5 overall: supported.** An independent recomputation, made directly from the per-request logs without the analysis code, reproduced both bounds exactly. k6's per-mode summary medians equalled the medians of the request logs in all 20 run × mode cases.

**Practical significance.** The two-sided intervals for Δ include zero at both lengths and are tens of milliseconds wide at 256 tokens. They are set by model noise, not by the envelope. With five blocks, the experiment can bound the overhead share below 1%, and below about 0.4–0.7%. It cannot resolve an effect of about 0.5 ms (0.01–0.08% of a request) directly.

**Instrumented share** (mechanistic, not the test). Median client plus server envelope time divided by E2E was **0.070%** for 32 tokens and **0.009%** for 256 tokens.

### 2.3 Where the variation comes from (exploratory, not pre-registered)

| Length | Δ outside the model, mean ms [95% CI] | min / max | Envelope time (aead), median ms |
|---|---|---|---|
| 32 tokens | +0.29 [−0.20, 0.77] | −0.35 / +0.69 | 0.45 |
| 256 tokens | +0.75 [+0.19, +1.31] | +0.22 / +1.31 | 0.53 |

- **Method.** "Outside the model" is E2E minus Ollama's `total_duration`. It covers client cryptography, TLS, the API, the envelope, and the 5–7 ms hop between the API and Ollama.
- **What it shows.** The differences between runs (−25 to +55 ms) lie almost entirely inside the model (correlation 0.997). The time outside the model differs by an amount close to the directly timed envelope work.
- **The 256-token interval** excludes zero. That is consistent with a small, real envelope cost: the larger response must be sealed, Base64-encoded and decrypted.
- **Caveat.** This analysis was chosen after the data were seen, so it is supporting evidence only.

**Noise was larger than in the pilot.**
- The full run's SD of log *r* (0.0086 and 0.0058) was about 10 times the pilot's (0.00062).
- Within runs, generation time had bursts. In the noisiest runs, the 95th percentile of generation time was up to about 620 ms above the run's median.
- A process snapshot taken during run 1 showed interactive desktop activity on the host: WindowServer at about 30% CPU, DisplayLink at about 12%, and the screen-capture service. These share the GPU.
- The pre-specified pilot rule (SD > 0.007) applied to the pilot only, and the pilot passed it. The H5 test itself accounts for the observed spread, and the bounds remained below 1%. This is a limitation of the host environment, not a protocol deviation.

### 2.4 Other quantities

| Quantity | Value |
|---|---|
| Δ fixed cost per request (aead − plain, two-length model) | −4.11 ms [−11.27, 3.06] |
| Δ per-token cost | +0.035 ms [−0.127, 0.197] |
| Generation rate | 46.6–52.5 tokens/s |
| GPU "Device Utilization" (median per run) | 94–96% |
| llama-server resident memory | up to 6,018 MiB |
| Request / response body | about 1,023 → 1,450 bytes; 416 → 641 (32 tokens) and 1,635 → 2,266 (256 tokens) |

- **The two-length model.** Only the aead − plain differences are reported, as approved at G7c. Its intercepts are not physically meaningful, because time per token grows with context length.
- **API CPU per request** is dominated by the container health check in this experiment (infer-pilot.md §4.1), so it is not reported as an outcome.

### 2.5 Limitations specific to H5

- Non-streaming only, so time to first token was not measured.
- One VU; concurrency was deferred.
- One quantized 8B model on an Apple GPU, with prompts of about 1 KiB.
- The host was in interactive use (§2.3).
- The study measures the API-tier envelope. The prompt is plaintext on the hop to the model (threat-model.md §5).
- A smaller share at larger models, longer outputs or slower hardware follows from the mechanism (fixed envelope cost against growing inference time), but was not measured.

## 3. Experiment D (A1): where the envelope cost goes

Server-side `EnvelopeCipher.seal` and `.open` from the study's API image (Python 3.12, `cryptography` with OpenSSL 4.0.3), run with 2 CPUs and no network. Values are the median and [min, max] across 5 processes.

| Plaintext | Envelope / plaintext bytes | seal µs | open µs | AES-GCM share (seal / open) | Base64 share (seal / open) | JSON share (seal / open) | Residual (seal / open) |
|---|---|---|---|---|---|---|---|
| 1 KiB prompt body | 1.407 | 3.79 | 3.30 | 11.2% / 12.9% | 20.3% / 33.7% | 57.8% / 44.6% | 5.6% / 8.9% |
| 1 KiB | 1.407 | 3.75 | 3.23 | 11.0% / 12.8% | 20.3% / 34.5% | 58.3% / 44.7% | 5.4% / 8.0% |
| 16 KiB | 1.338 | 30.38 | 28.21 | 7.6% / 8.3% | 30.0% / 50.2% | 60.7% / 38.9% | 1.0% / 1.9% |
| 256 KiB | 1.334 | 455.74 | 431.55 | 7.1% / 7.5% | 31.4% / 52.6% | 62.1% / 37.9% | −0.6% / 1.6% |

The remaining few percent of seal time, not shown, is nonce generation and AAD construction, under 0.2 µs together. Full table: `results/processed/20261021T162323Z-envelope-micro/breakdown.md`.

- **The cipher is cheap.** AES-GCM is cheap on this CPU, which has the ARMv8 Cryptography Extensions: 32 µs for 256 KiB, about 8 GB/s.
- **What dominates.** Most of the cost is Python serialisation:
  - `json.dumps` of an envelope that holds a large Base64 string (58–62% of sealing);
  - Base64 decoding with validation (34–53% of opening).
- **Consistency with Experiment A.** The microbenchmark totals are 0.5–0.9 times Experiment A's in-request `enc` and `dec` timers at 1 VU:

  | | 1 KiB | 16 KiB | 256 KiB |
  |---|---|---|---|
  | seal µs, micro vs in-request | 3.75 vs 5.80 | 30.4 vs 32.8 | 456 vs 653 |
  | open µs, micro vs in-request | 3.23 vs 6.70 | 28.2 vs 35.2 | 432 vs 525 |

  The order of magnitude agrees. In-request timings are higher, as expected with colder caches and allocation inside a running server.
- **Residual.** The small negative residual (−0.6%) for sealing 256 KiB shows that timing components separately is not perfectly additive. The cause is cache and allocator state. The residual is reported, not forced to zero.
- **Scope.**
  - The k6/WebCrypto client was not broken down.
  - The **network** cost of the envelope comes from its 33–41% larger bodies; wire sizes were measured in Experiments A and C.
  - A binary envelope (A2) was not implemented.
  - These figures say what *this* envelope costs and why. They do not measure an alternative design.

## 4. Interpretation (for the paper)

| Type | Statement |
|---|---|
| Measured | Against local 8B-model inference with responses of 32–256 tokens, the envelope's share of end-to-end latency is below 1% (UCB 0.36% and 0.65%). |
| Measured | Against the cheap deterministic workload of Experiment A, the same envelope added 13–52% to median latency. |
| Mechanistic (instrumented) | The envelope's absolute cost is about 0.5 ms per request at these body sizes. Its relative importance is set by the cost of the rest of the request. |
| Mechanistic (Experiment D) | Most of that cost is Base64 and JSON handling in Python, not AES-GCM. A binary or record-based envelope would be expected to cost less. That is untested here. |
| Extrapolated | For requests dominated by inference, which are longer, larger or slower than these, the share would be smaller. For many small non-inference calls, as in agent tool chains, it behaves like Experiment A. See [frontier-implications.md](../docs/notes/frontier-implications.md). |
| Not shown | Security benefit (no adversary was tested); streaming; concurrency; other hardware or models. |

## 5. Data quality and deviations

- **H5 runs.** All 10 runs passed criteria c1–c6:
  - zero errors, every aead response authenticated;
  - every request generated to its token limit;
  - the model stayed resident, with `load_duration` under 100 ms;
  - version and digest unchanged;
  - counts and order exactly as specified;
  - k6 CPU far below its limit.
- **Determinism.** Output hashes were consistent within every run, across both modes.
- **Deviations** from Phase 1, all approved before data: plan.md deviation 9 (lengths 32/256, concurrency 4 deferred, interleaved runs). None after data.
- **Corrections found during this phase:** k6 ignored `minVersion` in Experiments A and B. This was documented, and the results are unaffected (plan.md deviation 3).

## 6. Reproduction

```
make infer-determinism
make infer-pilot
make infer                                        # gated; owner-approved 2026-10-21
make h5 RUN=results/raw/20261021T155308Z-infer
uv run python scripts/envelope_micro.py
uv run python analysis/envelope_micro.py results/raw/20261021T162323Z-envelope-micro
```
