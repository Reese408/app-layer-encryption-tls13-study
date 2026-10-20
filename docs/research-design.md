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
| H5 | Under LLM inference, Δlatency as a share of total latency is not below 1%. | It is below 1%, so encryption is negligible next to inference. |

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
| Run protocol | Fixed warm-up, fixed measurement duration, N = 5 repetitions per cell, randomized interleaved order. |
| Host | Same machine, on AC power, with non-essential applications closed (recorded in the run manifest). |
| LLM (Phase 4) | Same model tag and digest, `temperature=0`, fixed `seed`, fixed `num_predict`, thinking disabled, identical prompts. |

## 4. Procedure

1. Run `make test` and confirm functional equivalence: for every fixture payload, the decrypted `aead` response body is byte-identical to the `plain` response body.
2. Run the smoke test: a short k6 run against both services with a 0% error threshold.
3. Run the **pilot** profile (`experiment.toml`: 3 sizes × concurrency {1, 64} × 2 repetitions × 2 modes = 24 runs, 5 s warm-up, 20 s measurement). The pilot confirms the pipeline, estimates variation between repetitions, and checks for client-side bottlenecks. The full matrix runs only after the pilot has been reviewed.
4. Run the full matrix: 2 modes × 3 sizes × 3 concurrency levels × 5 repetitions = **90 runs**. Each run has a 10 s warm-up, which is discarded, and a 60 s measurement window.
5. Write raw k6 output (end-of-test summary JSON with exact percentiles, plus logs), resource samples (CSV), and a run manifest (JSON) to `results/raw/<UTC timestamp>/`.
6. Run the analysis scripts, which read **only** from `results/raw/`.

## 5. Statistical analysis

- Per cell (mode × size × concurrency), each latency percentile is summarized across the 5 repetitions with its median and a bootstrap 95% confidence interval.
- Differences between modes are tested with the Mann–Whitney U test on per-repetition percentile values, and effect size is reported (Δ in ms and in %).
- The significance threshold is α = 0.05, with a Holm–Bonferroni correction across the 9 cells.
- Practical significance is reported next to statistical significance. A significant but sub-millisecond difference is described as such.

## 6. Threats to validity

- **Internal:** load-generator saturation, which is mitigated by recording client CPU. Thermal throttling and background processes, mitigated by interleaving and repetition.
- **Construct:** a loopback or Docker-bridge network has much lower latency than a real network, so *relative* overhead here is an upper bound on what a WAN deployment would see.
- **External:** one host (Apple Silicon, macOS, Docker Desktop VM). Results may differ on x86 or bare-metal Linux. The study has no real TLS-terminating intermediary, so it measures the cost of the layer, not its benefit in deployment.
- **Client-side cryptography:** in `aead` mode, k6 encrypts and decrypts with WebCrypto. That client cost is part of the end-to-end figure, and server-side timing is reported separately so it can be isolated.
