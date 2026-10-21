# Research Design

## 1. Background

TLS 1.3 (RFC 8446) protects data *in transit between two TLS endpoints*. In many real deployments the client's TLS session ends before the application, for example at a CDN, load balancer, API gateway, or service-mesh proxy. After that point the payload exists as plaintext inside infrastructure that the application owner may not fully control or audit. Application-layer authenticated encryption, such as AES-256-GCM over the request and response bodies, keeps the payload confidential and tamper-evident from one application endpoint to the other.

That protection is not free. The application must parse an envelope, perform an AEAD operation, Base64-encode or decode the result, and manage keys, all on top of the TLS record encryption that already happens. This study measures that extra cost.

## 2. Hypotheses

Let *Δ* denote the difference between the `aead` and `plain` configurations for a metric.

| ID | Null hypothesis (H₀) | Alternative (H₁) |
|---|---|---|
| H1 | Median latency is the same in both configurations. | `aead` has a higher median latency. |
| H2 | Tail latency (p95, p99) is the same. | `aead` has higher tail latency. |
| H3 | Sustained throughput (requests per second at fixed concurrency) is the same. | `aead` has lower throughput. |
| H4 | Δlatency does not depend on payload size. | Δlatency grows with payload size. |
| H5 | Under LLM inference, Δlatency as a share of total latency is not below 1%. | It is below 1%, so encryption is negligible next to inference. Operational definition and decision rule: §7.5. |

**Expected outcome, stated beforehand:** AES-GCM uses hardware acceleration on the test CPU (ARMv8 Cryptography Extensions), so the AEAD operation itself is expected to be cheap. Any overhead that can be measured is expected to come mostly from Python-level envelope handling (JSON and Base64) rather than from the cipher. This expectation is recorded *before* any data is collected and does not affect the analysis.

## 3. Variables

### 3.1 Independent variables

| Variable | Levels |
|---|---|
| Encryption mode | `plain`, `aead` |
| Request/response payload size | 1 KiB, 16 KiB, 256 KiB (plaintext) |
| Concurrency (k6 virtual users) | 1, 16, 64 |
| Workload (Phase 4 only) | deterministic CPU workload; LLM inference |

### 3.2 Dependent variables

- Client-observed latency: p50, p95, p99, mean, and max (`http_req_duration` in k6).
- Throughput: completed requests per second.
- Error rate: failed checks, non-2xx responses, and authentication failures.
- Server-side time breakdown, taken from `Server-Timing` headers: decrypt, workload, inference, and encrypt.
- Container CPU (%) and resident memory (MiB), sampled at 1 Hz.
- Load-generator CPU, recorded to confirm the client is not the bottleneck.

### 3.3 Controlled variables

| Variable | Control |
|---|---|
| Code and image | One Docker image. Mode is chosen at startup by configuration. |
| Workload | A deterministic function of the plaintext payload, identical in both modes and checked by tests. |
| TLS | TLS 1.3 only, same certificate, same cipher suite negotiation, keep-alive connections. |
| Server process | One Uvicorn worker, so the comparison measures per-process cost. |
| Container resources | Same `cpus` and `mem_limit` for both API services. |
| Payloads | Generated from a fixed seed, so the plaintext bytes are identical across modes. |
| Run protocol | Fixed warm-up, fixed measurement duration, N = 5 repetitions per cell, randomized interleaved order. Both API containers are recreated before every run (Section 4.1). |
| Host | Same machine, on AC power, with non-essential applications closed (recorded in the run manifest). |
| LLM (Phase 4) | Same model tag and digest, `temperature=0`, fixed `seed`, fixed `num_predict`, thinking disabled, identical prompts. |

## 4. Procedure

1. Run `make test` and confirm functional equivalence: for every fixture payload, the decrypted `aead` response body is byte-identical to the `plain` response body.
2. Run the smoke test: a short k6 run against both services with a 0% error threshold.
3. Run the **pilot** profile (`experiment.toml`: 3 sizes × concurrency {1, 64} × 2 repetitions × 2 modes = 24 runs, 5 s warm-up, 20 s measurement). The pilot confirms the pipeline, estimates variation between repetitions, and checks for client-side bottlenecks. It was executed on 2026-10-20 and reviewed by the repository owner; see [reports/pilot.md](../reports/pilot.md).
4. Apply the methodology refinements approved after the pilot review (Section 4.1) and validate them on reduced closed-loop and open-loop profiles (`make validate-pipeline`, `make check-run`). The full matrix runs only after the owner approves that validation.
5. **Experiment A (primary): the closed-loop matrix.** 2 modes × 3 sizes × 3 concurrency levels × 5 repetitions = **90 runs**. Each run has a 10 s warm-up, which is discarded, and a 60 s measurement window.
6. **Experiment B (secondary, separate): the open-loop fixed arrival rate.** 2 modes × 3 sizes × 5 repetitions = 30 runs, with the same warm-up and window. It is analysed separately and never pooled with Experiment A (Section 4.1).
7. Write raw k6 output (end-of-test summary JSON with exact percentiles, a deterministic per-request sample, and logs), resource samples (CSV), and a run manifest (JSON) to `results/raw/<UTC timestamp>-<profile>/`.
8. Run the analysis scripts, which read **only** from `results/raw/`.

### 4.1 Refinements approved after the pilot (2026-10-20)

| ID | Change | Justification | Resulting limitation |
|---|---|---|---|
| R1 | **Per-run restart.** Both API containers are recreated before every run. The orchestrator waits for the Docker health check (every 250 ms during start-up), then for a host-side `/healthz` probe over verified TLS 1.3, then records a 2 s idle resource baseline before k6 starts. k6's warm-up is unchanged. Restart, readiness, k6 launch, k6 start (*t₀*), warm-up end, measurement end, and container identities are recorded per run. | In the pilot, API memory depended on run history (52–65 MiB versus 130–153 MiB for the same configuration). A fresh process makes runs independent, which the run-level statistics (Section 5) assume. | Each run measures a process that has served only its own warm-up. Long-lived-process effects (heap growth, fragmentation) are outside the scope. Runs are about 5 s longer. |
| R3 | **Open-loop experiment (Experiment B).** k6 `constant-arrival-rate` at a fixed rate per size: 7,000, 1,900, and 140 requests/s for 1 KiB, 16 KiB, and 256 KiB. These are about 50% of the *lower* pilot capacity, which was the aead configuration's (14,946, 3,880, and 294 requests/s at 64 VUs), rounded down, so both configurations run below capacity. Each run consists of two consecutive k6 scenarios: a `warmup` scenario (`ramping-arrival-rate`, 0 → rate over the first half of the warm-up, then the full rate, ending 0.5 s early so that in-flight iterations finish) and a `measure` scenario (`constant-arrival-rate`) for the measurement window. The same VUs, with their established TLS connections, serve both. The pre-allocated VU pool is max(64, ⌈rate × 0.1 s⌉) = 700, 190, and 64 VUs, enough to absorb a 100 ms stall without dropping arrivals; no VUs are allocated mid-run. Any dropped iteration in the measurement scenario fails the run; warm-up drops, measurement-window drops, and the achieved rate are recorded. The sample interval *K* is 23, 23, and 3, coprime with both the payload pool and the VU pool. These details were settled in four validation attempts (see [pilot report §10.3](../reports/pilot.md#103-validation-of-the-revised-pipeline)). | In the closed loop at 64 VUs, latency is dominated by queueing (Little's law), and at 1 VU the client's own crypto paces the loop. A fixed arrival rate measures per-request overhead at a realistic, non-saturating load. | Utilization differs between modes at the same rate (the aead server is busier), so some of the latency difference is queueing that the extra CPU cost causes. That is a real effect, but it is not pure per-request cost. The rates are derived from one pilot on one host. A pool sized for 100 ms stalls means that longer stalls of the host's virtual machine would still drop arrivals and fail the run; such runs are excluded and reported, not repaired. |
| R6 | **Bounded per-request sample.** Deterministic systematic sampling: a measured request is kept when its global iteration index is divisible by *K* (101, 25, or 3 by size in the closed loop; 23, 23, or 3 in the open loop; always coprime with the 4-item payload pool, and in the open loop also with the VU pool, which is reused in rotation). The cap is 50,000 per run. The k6 summaries are preserved unchanged. | It enables distributional views and per-request breakdowns (client, server, and network time) without about 10⁷ rows per run. Deterministic selection is reproducible, and it is independent of latency because the index is assigned when an iteration starts. | **It is a sample, not the request population,** and is always labelled as such. All reported percentiles come from the exact k6 summary. Writing about 0.1–1% of requests to the log adds a small, equal client cost in both modes. |
| (P1) | The client encrypt/decrypt timers use `exec.instance.currentTestRunDuration` (monotonic, sub-µs), not `Date.now()` (1 ms). | k6 exposes no `performance.now()`. This clock was found during the R6 work. | Client-timer percentiles from the pilot are not comparable with later runs; only the pilot's means are. |

## 5. Statistical analysis

This plan was fixed after the pilot review and **before** the full matrix was run. It replaces the Phase 1 plan, which used unpaired Mann–Whitney U tests on per-repetition values.

**Unit of analysis.** The unit is one run. Requests within a run share a process, connection pool, and time window, so they are serially dependent. Treating them as independent observations would understate uncertainty by orders of magnitude. Each run contributes one value per metric: k6's exact p50, p95, or p99 over the measurement window; throughput; API CPU-ms per request; or peak API working set.

**Design.** Each repetition is a randomized block that contains every cell once, in a seeded random order. Within a block, the aead run and the plain run of the same cell form a pair, so slow drift between blocks (thermal state, background load) cancels out. For Experiment A, *n* = 5 pairs per cell.

**Effect sizes** (per cell and metric):

- *Ratio* = the geometric mean over blocks of aead/plain, which is exp of the mean per-block log ratio. It is reported as % change. This is the primary effect size, because performance effects are multiplicative and the cells span three orders of magnitude.
- *Absolute difference* = the mean per-block difference aead − plain, in the metric's units (ms, requests/s, CPU-ms, MiB).
- *Cliff's δ* (unpaired, from −1 to 1), a scale-free and distribution-free measure of separation.

**Confidence intervals.** 95% CIs for the ratio and the difference come from the paired *t* distribution on per-block log ratios (or differences), with *df* = *n* − 1. For each family, a Bonferroni-simultaneous CI at level 1 − 0.05/*m* is also reported for the ratio.

**Hypothesis tests.** H1–H3 use a two-sided paired *t*-test on the per-block log ratios. Two-sided tests are used even though the hypotheses are directional, which is the conservative choice.

**Multiple comparisons.** A family is one metric across all cells of one experiment. In Experiment A, H1 (p50), H2 (p99), and H3 (throughput) are each a family of *m* = 9 cells, and Holm–Bonferroni controls the family-wise error rate at α = 0.05 within each family. Secondary families (p95, end-to-end p50, API CPU-ms per request, peak working set) use the same procedure and are labelled exploratory. Experiment B's families (*m* = 3) are separate and do not include throughput, which is fixed by design.

**H4 (size dependence).** For each concurrency level, the per-block Δp50 (ms) is regressed on log₂(payload bytes) by OLS, which gives a slope in ms per doubling with a 95% CI. Holm correction is applied across the concurrency levels.

**Robustness checks** are reported, but no decision rests on them:

- the number of blocks in which aead > plain;
- an exact sign-flip permutation p on the log ratios (with *n* = 5 the smallest attainable two-sided p is 2/32 = 0.0625, so this check cannot reach α = 0.05 by itself);
- an exact Mann–Whitney U p, unpaired (smallest attainable 2/252 ≈ 0.008). SciPy's exact method assumes no ties; run-level values are continuous, so ties are not expected, but if any occur this p is approximate.

**Practical significance** is reported next to statistical significance. A statistically significant difference smaller than 0.1 ms is described as such.

**Assumptions.** The *t* procedures assume approximately normal per-block log ratios, which cannot be verified with *n* = 5. The permutation and rank checks do not make that assumption. If the two disagree, both results are reported.

Implementation: `analysis/stats.py` (tested in `tests/test_analysis.py`).

## 6. Threats to validity

- **Internal:** load-generator saturation, which is mitigated by recording client CPU and, in the open loop, by failing any run with dropped iterations. Thermal throttling and background processes, mitigated by interleaving, blocking, and repetition. Run-history effects, mitigated by recreating the containers before each run (R1).
- **Construct:** a loopback or Docker-bridge network has much lower latency than a real network, so *relative* overhead here is an upper bound on what a WAN deployment would see.
- **External:** one host (Apple Silicon, macOS, Docker Desktop VM). Results may differ on x86 or bare-metal Linux. The study has no real TLS-terminating intermediary, so it measures the cost of the layer, not its benefit in deployment.
- **Client-side cryptography:** in `aead` mode, k6 encrypts and decrypts with WebCrypto. That client cost is part of the end-to-end figure, and server-side timing is reported separately so it can be isolated.
- **Statistical power:** five pairs per cell detect large, consistent effects (the pilot showed 5–48% median-latency differences with low variation between repetitions), but have little power for small effects or for p99 at 1 VU, which varied by up to 55% between pilot repetitions. A non-significant result is not evidence of no effect; the confidence intervals bound the plausible effect.

## 7. Experiment C: LLM inference (H5), pre-registered protocol

This section was approved by the repository owner on 2026-10-21 and committed **before any H5 measurement was collected**. Two exploratory requests were sent to Ollama before this commit, to confirm that the containers can reach the host and to estimate runtime: about 52 generated tokens/s, roughly 0.7 s for a 32-token response and 5.1 s for a 256-token response. They are feasibility checks only and are not H5 data.

### 7.1 Model and inference controls

| Control | Value |
|---|---|
| Runtime | Ollama 0.35.0, running natively on the macOS host (Metal GPU). The version is checked before every run, and the run is aborted if it has changed, which guards against auto-update during the study. |
| Model | `qwen3:8b`, digest `sha256:500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41`, GGUF Q4_K_M, 8.2B parameters. The digest is checked before every run. |
| Generation options (every request, including warm-up) | `think=false`, `stream=false`, `temperature=0`, `seed=42`, `top_k=20`, `top_p=0.95`, `repeat_penalty=1`, `num_ctx=4096`, `keep_alive=-1`; `num_predict` set by the response-length level |
| Residency | Before and after every run, `/api/ps` must show the pinned model loaded and fully in GPU memory (`size_vram == size`). A changed `num_ctx` would force a reload, so it is never varied. |
| Prompts | Four fixed prompts of about 1 KiB each (`scripts/gen_prompts.py`), written so that generation runs to the token limit (`done_reason=length`) |

### 7.2 Factors and design

- **Encryption mode:** `plain`, `aead` (unchanged).
- **Response length:** `num_predict` = 32 (*short*) and 256 (*long*). This replaces the single 128-token level of `plan.md` 4.1 (owner-approved deviation). The short level is the least favourable case for H5, because fixed per-request costs are the largest share there. With two levels, the fixed and per-token components of latency can be separated.
- **Concurrency:** one virtual user. The concurrency-4 cell of `plan.md` 4.2 is deferred (owner-approved). Ollama serves one model on one GPU. With concurrent requests it queues or batches them, and batching changes the numerics, so the work in each mode would no longer be equivalent.
- **Interleaved runs (owner-approved deviation from Experiments A and B).** One k6 process sends requests alternately to `api-plain` and `api-aead`, one at a time. Experiments A and B were CPU-bound, and run-level pairing controlled drift adequately there. Here one shared GPU dominates latency, and H5 is a threshold claim at 1%, so drift between runs would otherwise set the confidence bound. Each run is still the unit of analysis.
- **Request order inside a run.** The 8-request cycle (p1,A) (p2,B) (p3,A) (p4,B) (p1,B) (p2,A) (p3,B) (p4,A) is repeated, where A is the run's first mode. Each mode receives each prompt equally often, and no request follows a request with the same prompt. Ollama reuses its prompt cache when consecutive requests share a prefix, so a repeated prompt would skip prompt processing and bias the comparison. Only the common chat-template prefix can be reused, and it is reused equally in both modes. The first mode alternates between blocks, with a seeded starting point per length.
- **Per run:** one 8-request cycle of warm-up (discarded), then 48 measured requests, which is 24 per mode. Both API containers are recreated before every run (R1).
- **Blocks:** each repetition is a block that contains the short run and the long run in a seeded random order. The full experiment has 5 blocks, which makes 10 runs and 480 measured requests. The pilot has 3 blocks with 32 measured requests per run.

### 7.3 Determinism check (before the pilot)

Each prompt × length combination is generated 5 times directly against Ollama, cycling through the prompts so that no prompt repeats back to back. **Required:** every generation has `eval_count == num_predict` and `done_reason=length`, which shows equal generation work. **Reported:** whether the SHA-256 of the generated text is identical across repeats. The text itself is not stored. A fixed seed does not guarantee byte-identical output, and identical hashes are not required for validity.

### 7.4 Measures

| Measure | Definition |
|---|---|
| **E2E** (primary) | k6 `iteration_duration` of one request: client encryption (aead), HTTP request, client decryption (aead), and response checking |
| HTTP (secondary) | k6 `http_req_duration` |
| Client crypto | k6 encrypt and decrypt timers |
| Server crypto | `Server-Timing` `dec` and `enc` |
| Model wait | `Server-Timing` `infer`: the wall time of the API's call to Ollama |
| Model internals | Ollama's `load_duration`, `prompt_eval_count`/`_duration`, `eval_count`/`_duration`, `total_duration` (returned inside the response body, so they are encrypted in aead mode) |
| Network hop to the model | `infer` − `total_duration` |
| Tokens/s | `eval_count` / `eval_duration` |
| Resources | API and k6 containers (Docker stats, ~1 Hz); the Ollama host processes' RSS and cumulative CPU time (`ps`, 1 Hz); GPU "Device Utilization %" and in-use memory from `ioreg` (IOAccelerator, 1 Hz, unprivileged, driver-reported, semantics not publicly documented) |

**Time to first token is not measured.** Responses are non-streaming, so the client receives the whole response at once. Ollama's `prompt_eval_duration` is reported as server-side prompt processing only and is not presented as time to first token.

Every measured request is recorded, so run-level medians are computed from the complete request log and not from a sample. Per-mode medians from the k6 summary are a cross-check.

### 7.5 Statistical procedure for H5

For run *b* (block) at length *L*, let *r_b* = median E2E(aead) / median E2E(plain), each median taken over that run's 24 measured requests per mode.

- **Overhead share:** *s* = Δ / T_aead = 1 − 1/*r*. The threshold *s* < 1% is equivalent to *r* < 1/0.99 = 1.010101.
- **Hypotheses, per length level:** H₀: *s* ≥ 1%; H₁: *s* < 1%.
- **Decision:** compute the one-sided 95% upper confidence bound (UCB) on the geometric-mean ratio, exp(mean(log *r_b*) + *t*₀.₉₅,₄ · sd(log *r_b*) / √5).
  - **Supported** at length *L* if UCB < 1.010101.
  - **Rejected** if the one-sided 95% lower bound is ≥ 1.010101.
  - **Inconclusive** otherwise.
- **H5 overall** is supported only if it is supported at **both** lengths (an intersection–union test, so no multiplicity correction is needed), rejected if it is rejected at either length, and inconclusive otherwise. A non-significant difference test is never taken as support.
- **Also reported:**
  - the absolute difference (ms) and the relative difference, with two-sided 95% CIs;
  - the per-run log ratios and their SD;
  - the same procedure applied to HTTP latency (secondary);
  - an **instrumented share**: (client enc + client dec + server dec + server enc) / E2E(aead), per request, median per run. This is a mechanistic estimate, not the test;
  - an OLS model E2E = *a* + *b*·tokens per mode, separating fixed from per-token cost;
  - practical significance in milliseconds.
- **Pilot rule (fixed now):** if the pilot's pooled SD of log *r_b* exceeds 0.007 (UCB margin under ~0.3% at *n* = 5), the owner is asked whether to raise the number of blocks, with a precision calculation. The test itself is not changed.

### 7.6 Run acceptance criteria

A run is valid only if all of the following hold. Invalid runs are kept, reported, and never repaired.

1. Zero HTTP errors, all checks passed, and every aead response authenticated and decrypted.
2. Every measured request has `eval_count == num_predict` and `done_reason=length`.
3. Every measured request has `load_duration` < 100 ms, and the model is resident on the GPU before and after the run.
4. The Ollama version and model digest are unchanged from the session start.
5. Each mode received 24 measured requests (6 per prompt), in the pre-specified order.
6. k6 CPU stayed below 85% of its limit.
