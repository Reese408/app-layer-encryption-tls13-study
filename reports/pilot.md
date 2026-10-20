# Pilot Report: AES-256-GCM Application-Layer Encryption over TLS 1.3

| | |
|---|---|
| Run | `results/raw/20261020T154305Z-pilot/` (24 runs, 2026-10-20 15:43–15:54 UTC) |
| Code | commit `2d16056` (clean working tree, per `manifest.json`) |
| Aggregation | `uv run python analysis/aggregate.py results/raw/20261020T154305Z-pilot` → `results/processed/20261020T154305Z-pilot/` |
| Status | **Preliminary.** There are two repetitions per cell and no significance testing. These results are for checking the method, not for drawing conclusions. |

## 1. Purpose

The pilot had four goals:

1. Confirm that the measurement pipeline produces complete, valid raw data in both configurations.
2. Estimate how much results vary between repetitions, so the full-matrix design (5 repetitions × 60 s) can be checked.
3. Find out whether the load generator, rather than the system under test, limits any cell.
4. Find problems in the method before the 90-run matrix is run.

## 2. Configuration

| Parameter | Value |
|---|---|
| Modes | `plain` (TLS 1.3 only), `aead` (TLS 1.3 + AES-256-GCM envelope) |
| Payload sizes (plaintext request body) | 1 KiB, 16 KiB, 256 KiB |
| Concurrency (closed-loop k6 VUs) | 1, 64 |
| Repetitions | 2, in randomized block order (seed 20261020) |
| Warm-up / measurement / cool-down | 5 s / 20 s / 3 s |
| Workload | Canonical JSON and a 16-round SHA-256 chain over the request, then echo |
| API | FastAPI 0.153.0, Uvicorn 0.54.0 (uvloop and httptools, 1 worker), Python 3.12, `cryptography` 50.0.2 |
| Load generator | k6 v2.3.0 (container), WebCrypto AES-GCM, fresh random nonce per request |
| Limits | API: 2 CPUs, 1 GiB each. k6: 8 CPUs, 3 GiB. |
| Host | Apple M5 Pro (18 cores, 64 GiB), macOS 26.6, Docker Desktop 29.8.1 VM (18 vCPU, ~7.7 GiB), on AC power |
| Negotiated TLS | TLSv1.3, `TLS_AES_256_GCM_SHA384`, in every run |

## 3. Commands executed

```bash
uv run pytest                                   # 53 passed
uv run python scripts/validate_env.py           # TLS + sampler validation -> results/raw/20261020T154126Z-validation/
uv run python scripts/run_matrix.py --profile smoke   # 12/12 passed strict thresholds -> results/raw/20261020T153834Z-smoke/
uv run python scripts/run_matrix.py --profile pilot   # 24/24 k6 exit 0 -> results/raw/20261020T154305Z-pilot/
uv run python analysis/aggregate.py results/raw/20261020T154305Z-pilot
```

## 4. Validation evidence

| Property | Method | Result |
|---|---|---|
| AES-256-GCM correctness | Known-answer tests (McGrew–Viega test cases 13 and 14) in Python. Test case 14 was also reproduced independently in k6 WebCrypto. | Pass |
| Encryption round trip | 0 B to 256 KiB, both directions | Pass |
| Nonce handling | 20,000 seals give 20,000 distinct 96-bit nonces and ciphertexts. The usage ceiling fails closed. | Pass |
| Authentication failures | Altered ciphertext, tag, or nonce; truncated tag; wrong method or route in the AAD; reflected response; wrong key; wrong `kid`; malformed input. All produce the same generic `invalid_envelope` error. | Pass |
| Functional equivalence | The decrypted `aead` response is byte-identical to the `plain` response and to the reference workload output | Pass |
| Output integrity under load | k6 compares every response byte-for-byte with the expected output | 100% of requests in all 24 runs |
| Integrity check can fail | The expected output was deliberately corrupted | k6 exit 99, integrity = 0 (both modes) |
| TLS 1.3 negotiation | `openssl s_client` against both containers; Python test against the real launcher; k6 check on every response | TLS 1.3 negotiated and certificate verified. TLS 1.2 refused. |
| Test sensitivity | Logging plaintext, or removing the TLS 1.3 minimum, makes the corresponding tests fail | Confirmed |
| Resource sampler | A known load (1 CPU busy loop plus 256 MiB) compared against `docker stats` | 99.8% of one core (docker stats 100.2%). 276 MiB peak (docker stats 263 MiB). |
| Secrets | `results/` was searched for key material and private keys | None found |

## 5. Results

All values are medians of two repetitions. Latency is k6 `http_req_duration` in ms.

### 5.1 Latency and throughput

| Size | VUs | p50 plain | p50 aead | Δp50 | p99 plain | p99 aead | Δp99 | req/s plain | req/s aead | Δ req/s |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 KiB | 1 | 0.120 | 0.127 | +0.007 (+5.6%) | 0.165 | 0.176 | +0.010 (+6.3%) | 6114 | 4677 | −23.5% |
| 1 KiB | 64 | 3.275 | 4.011 | +0.736 (+22.5%) | 6.667 | 8.231 | +1.564 (+23.5%) | 18536 | 14946 | −19.4% |
| 16 KiB | 1 | 0.233 | 0.322 | +0.088 (+37.8%) | 0.321 | 0.594 | +0.273 (+85.3%) | 3554 | 1649 | −53.6% |
| 16 KiB | 64 | 11.654 | 15.660 | +4.006 (+34.4%) | 23.283 | 31.763 | +8.480 (+36.4%) | 5367 | 3880 | −27.7% |
| 256 KiB | 1 | 2.181 | 3.170 | +0.989 (+45.4%) | 2.682 | 3.586 | +0.903 (+33.7%) | 436 | 165 | −62.1% |
| 256 KiB | 64 | 145.387 | 215.302 | +69.915 (+48.1%) | 153.702 | 223.742 | +70.040 (+45.6%) | 440 | 294 | −33.2% |

No cell had any errors (error rate 0.0000 in all 24 runs).

### 5.2 Server-side cost breakdown (aead, mean ms per request)

| Size | VUs | Server decrypt | Workload | Server encrypt | Crypto share of server compute | API CPU-ms/req plain → aead (Δ) |
|---|---|---|---|---|---|---|
| 1 KiB | 1 | 0.0067 | 0.0176 | 0.0058 | 41.5% | 0.092 → 0.103 (+0.010) |
| 1 KiB | 64 | 0.0048 | 0.0158 | 0.0051 | 38.5% | 0.055 → 0.068 (+0.013) |
| 16 KiB | 1 | 0.0357 | 0.1270 | 0.0328 | 35.1% | 0.207 → 0.307 (+0.100) |
| 16 KiB | 64 | 0.0319 | 0.1278 | 0.0330 | 33.7% | 0.190 → 0.263 (+0.073) |
| 256 KiB | 1 | 0.4436 | 1.8314 | 0.4519 | 32.8% | 2.167 → 3.199 (+1.032) |
| 256 KiB | 64 | 0.4883 | 1.9674 | 0.5812 | 35.2% | 2.319 → 3.485 (+1.166) |

The workload's own time is the same in both modes to within about 1%, except the 1 KiB / 1 VU cell, where the difference is 3 µs. This supports the claim that both configurations do the same application work.

### 5.3 Client-side cost and load-generator headroom

| Size | VUs | Client overhead plain → aead (ms) | Client share of aead E2E | k6 CPU (% of its 8-CPU limit), max of modes | API CPU (% of one core) |
|---|---|---|---|---|---|
| 1 KiB | 1 | 0.041 → 0.083 | 39.2% | 9.5% | 47.9–56.4 |
| 1 KiB | 64 | 0.043 → 0.097 | 2.3% | 29.4% | 102.0–102.3 |
| 16 KiB | 1 | 0.043 → 0.271 | 44.7% | 10.0% | 50.5–73.4 |
| 16 KiB | 64 | 0.047 → 0.268 | 1.6% | 24.5% | 102.0 |
| 256 KiB | 1 | 0.083 → 2.854 | 47.1% | 8.2% | 52.8–94.5 |
| 256 KiB | 64 | 0.075 → 2.503 | 1.1% | 11.8% | 102.0–102.3 |

Client overhead is `iteration_duration − http_req_duration`, which comes from k6's high-resolution timers. The loop-timed microbenchmark in the same session measured client seal/open costs of 0.024/0.016 ms (1 KiB), 0.152/0.058 ms (16 KiB), and 2.168/0.618 ms (256 KiB) per operation.

### 5.4 Wire size

The envelope Base64-encodes the ciphertext, so bytes per request grow by about a third in each direction:

| Size | Sent B/req plain → aead | Received B/req plain → aead |
|---|---|---|
| 1 KiB | 1,181 → 1,608 (+36%) | 1,365 → 1,835 (+34%) |
| 16 KiB | 16,564 → 22,133 (+34%) | 16,749 → 22,342 (+33%) |
| 256 KiB | 262,655 → 350,254 (+33%) | 262,842 → 350,464 (+33%) |

### 5.5 Variation between repetitions

The range between the two repetitions, as a percentage of the median, was:
- p50 latency: 0.0–5.8%
- throughput: 0.1–7.8%
- p99 latency: up to 55.2% (aead, 16 KiB, 1 VU) and 22.1% (plain, 1 KiB, 1 VU)

Tail latency at 1 VU is the least stable measurement.

## 6. Interpretation (preliminary)

1. **The two concurrency levels measure different things.**
   - **64 VUs** saturates the single-worker server in every cell: API CPU is about 102% of one core, and the client is idle by comparison (≤ 29% of its limit). Mean latency matches Little's law (64 ÷ throughput) to within 3% in every cell, so latency at 64 VUs is mostly queueing. The throughput reduction there (−19% at 1 KiB, −28% at 16 KiB, −33% at 256 KiB) measures **server capacity cost**.
   - **1 VU** is a closed loop with one request in flight at a time. Its throughput is the inverse of end-to-end time, which includes k6's own encryption. At 256 KiB, client work is about 47% of aead end-to-end time. **The 1 VU throughput deltas mostly reflect the cost to the client, not the server.** The 1 VU latency figures (`http_req_duration`) are the clean per-request overhead measurement.
2. **Measured server cryptography explains most of the server capacity loss.** Δ API CPU-ms per request comes from container CPU and throughput. It is close to the server's own decrypt+encrypt timing. At 64 VUs, where the server is busy all the time and the CPU-per-request estimate is most reliable, it is +9% to +31% higher. At 1 VU it ranges from −20% to +46%, and the 16 KiB cell is the outlier. The excess is consistent with parsing and serializing an envelope that is about 33% larger. At 1 VU, idle event-loop time makes the CPU-per-request estimate less precise.
3. **The cost scales with payload size.** Server decrypt+encrypt took about 0.01 ms at 1 KiB, 0.07 ms at 16 KiB, and 0.9–1.1 ms at 256 KiB, roughly 4 ms per MiB of plaintext. This is consistent with H4, pending the full matrix and statistical testing.
4. **The relative overhead is large because the baseline is very cheap.** The workload costs 0.02–2 ms per request. Against that baseline, the AEAD layer and its Base64/JSON envelope come to about a third of server compute time. Against a heavier workload, such as LLM inference (Phase 4), the same absolute cost would be a much smaller share. The pilot does not test that.
5. **TLS 1.3 already negotiates AES-256-GCM** (`TLS_AES_256_GCM_SHA384`). The aead configuration therefore encrypts the payload twice with the same algorithm. The added security comes from where the second layer ends (at the application), not from a stronger primitive. See [threat-model.md](../docs/threat-model.md).

## 7. Problems encountered and how they were resolved

| # | Problem | Resolution |
|---|---|---|
| P1 | k6 v2.3 has no `performance.now()`, so per-request client timers have 1 ms resolution | Per-request client timers are reported as means only. Client cost is measured as `iteration_duration − http_req_duration` and with a loop-timed microbenchmark. |
| P2 | k6 v2.3 rejects the `maxVersion` TLS option | Removed. TLS 1.3 is still enforced by k6 `minVersion`, the server's minimum version, and a per-request `tls_version` check. |
| P3 | At more than 10⁴ req/s, per-request k6 CSV output would be about 10⁷ rows per run | Dropped. The k6 summary, whose percentiles are exact over all measured samples, is the raw record. **Open item for review.** |
| P4 | k6 `handleSummary` runs in a separate JavaScript runtime, so the microbenchmark lost its module-level results | Results are now emitted as tagged k6 metrics |
| P5 | The handler-level `app` timer includes awaiting the request body, so it absorbs queueing when the server is saturated. The first aggregation showed a misleading 1.0% crypto share at 256 KiB / 64 VUs. | The share is now computed against decrypt + work + encrypt. `app` is retained as raw data but not used as a denominator. |
| P6 | **API memory depends on run history.** The aead container's first two runs peaked at 52–65 MiB, and later runs reached 130–153 MiB. The containers persist across runs. | **Not resolved.** Memory comparisons from the pilot are not valid. See R1. |
| P7 | The client microbenchmark varies between sessions (256 KiB seal: 1.76 ms/op earlier versus 2.17 ms/op in the pilot) | Recorded. See R4. |
| P8 | The first progress monitor matched per-run `exit=` text and reported completion early | Fixed by watching for the final exit line. The data was not affected. |

## 8. Recommendations before the full matrix (owner decision required)

- **R1 (method change): restart both API containers before every run.** This removes the run-history confound on memory (P6) and makes runs independent. The cost is about 5 s per run, roughly 8 minutes for 90 runs.
- **R2 (interpretation): report 1 VU as latency and 64 VUs as capacity.** Keep 16 VUs as planned. The smoke run already showed saturation at 8 VUs, so 16 VUs is expected to be server-bound too.
- **R3 (optional design addition): an open-model run** (`constant-arrival-rate`) at a fixed rate below plain-mode capacity, for example 50%. This would measure latency overhead without queueing amplification. It needs approval because it adds cells.
- **R4: run the client microbenchmark at both the start and the end of each session**, and report the spread.
- **R5: keep 5 repetitions × 60 s.** p50 and throughput vary by less than 8% between repetitions. p99 at 1 VU varies by up to 55%, which the longer window and extra repetitions should reduce. Statistical tests (Mann–Whitney U with Holm correction) are planned for the full matrix.
- **R6: decide on P3** (summary-only raw record versus a sampled or time-bucketed per-request export).

Estimated full-matrix duration: 90 runs × about 77 s, or about 2 hours (about 2 h 10 min with R1).

## 9. Limitations

- Two repetitions per cell, so no inferential statistics.
- One host, loopback networking inside the Docker Desktop VM. Network latency is negligible, so *relative* overhead is an upper bound compared with a real network.
- A single-worker server, so these are per-process capacity figures.
- No TLS-terminating intermediary is emulated, so the security benefit is argued from the threat model, not measured.
- Static, pre-shared symmetric keys; no key agreement or rotation (see the threat model).
