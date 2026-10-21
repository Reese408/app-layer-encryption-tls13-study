# Implementation Plan

Status markers: ☐ not started · ◐ in progress · ☑ done (verified by execution)

## Phase 1: Planning and environment

- ☑ Inventory of Docker, Python, Ollama, k6, and related tools ([environment.md](environment.md))
- ☑ Research design, hypotheses, and controlled variables ([research-design.md](research-design.md))
- ☑ Architecture ([architecture.md](architecture.md)) and threat model ([threat-model.md](threat-model.md))
- ☑ README.md, AGENTS.md, repository skeleton
- ☑ Plan review and approval by the repository owner (2026-10-20: defaults D1–D5 accepted, with a pilot before the full matrix)

## Phase 2: Minimum experiment (☑ implemented on branch `phase-2-minimum-experiment`, PR #1)

| # | Deliverable | Files |
|---|---|---|
| 2.1 | Python project, pinned dependencies (`fastapi`, `uvicorn`, `cryptography`, `pytest`, `httpx`, `pandas`, `scipy`, `matplotlib`, `ruff`) | `pyproject.toml`, `uv.lock` |
| 2.2 | Local CA and server certificate generation | `scripts/gen_certs.sh` |
| 2.3 | AEAD key generation (two directional keys plus a key ID) | `scripts/gen_keys.py` |
| 2.4 | FastAPI service with the deterministic workload and a TLS 1.3-only launcher | `app/main.py`, `app/workload.py`, `app/server.py` |
| 2.5 | AES-256-GCM envelope module (seal/open, AAD, usage ceiling, generic errors) | `app/crypto_envelope.py` |
| 2.6 | `Server-Timing` stage instrumentation (in the request handler) | `app/main.py` |
| 2.7 | Dockerfile and Compose file (two services, same image, equal limits, secrets) | `Dockerfile`, `compose.yaml` |
| 2.8 | k6 scripts sharing one scenario, mode switched by environment (WebCrypto AES-GCM on the client in aead mode) | `k6/bench.js`, `k6/lib/envelope.js` |
| 2.9 | Tests: envelope round trip, tamper/AAD/wrong-key rejection, nonce uniqueness, usage ceiling, no-secret-in-logs, TLS 1.3 enforcement, plain/aead functional equivalence | `tests/` |
| 2.10 | Run orchestrator, randomized order, manifest, raw export | `scripts/run_matrix.py`, `scripts/sample_resources.py` |
| 2.11 | Makefile targets | `Makefile` |
| 2.12 | Validation evidence (TLS negotiation, sampler accuracy) | `scripts/validate_env.py`, `scripts/validate_sampler.py` |
| 2.13 | Client-side crypto microbenchmark | `k6/client_crypto_bench.js` |
| 2.14 | Smoke (12 runs) and pilot (24 runs) executed; per-run aggregation | `experiment.toml`, `analysis/aggregate.py`, `reports/pilot.md` |
| ☑ | Owner review of the pilot (2026-10-20): R1, R3 (separate experiment), R5, and R6 (with limits) approved; run-level statistics requested | `reports/pilot.md` §10 |
| 2.15 | Per-run container recreation, readiness probe, idle baseline, and per-run timestamps (R1) | `scripts/run_matrix.py`, `compose.yaml` |
| 2.16 | Open-loop fixed-arrival-rate executor and profiles, kept apart from the closed-loop matrix (R3) | `k6/bench.js`, `experiment.toml` |
| 2.17 | Deterministic, bounded per-request sample (R6) and a high-resolution client timer | `k6/bench.js`, `scripts/run_matrix.py` |
| 2.18 | Paired run-level statistics and pre-specified acceptance checks | `analysis/stats.py`, `analysis/validate_pipeline.py`, `tests/test_analysis.py` |
| ☑ | Validation of the revised pipeline executed: closed loop 24/24; open loop 11/12 on the fourth attempt, with one R6(d) sample-quantile failure documented | `results/raw/*-validate-*`, `reports/pilot.md` §10.3 |
| ☑ | Owner approval of the validation (2026-10-20): accepted as reported, including the one R6(d) failure; the full matrix and then the open-loop experiment approved | `reports/pilot.md` §10.3 |

### Deviations from the Phase 1 design (recorded during implementation)

1. **Per-request k6 CSV dropped.** At more than 10⁴ requests per second it would produce about 10⁷ rows per run. The k6 end-of-test summary, whose percentiles are exact over all measured samples, is the raw per-run record.
2. **Client-side crypto timing.** k6 v2.3 has no `performance.now()`. Per-request client encrypt/decrypt timers have 1 ms resolution and are reported as means only. A high-resolution figure comes from `iteration_duration − http_req_duration` and from a loop-timed microbenchmark.
3. **TLS max version option.** k6 v2.3 rejects `maxVersion`. TLS 1.3 is enforced by k6's `minVersion`, by the server's `minimum_version`, and by a per-request `tls_version` check. **Correction (2026-10-21):** k6 v2.3 also ignores `minVersion`. Every Experiment A and B log reports `unknown field "minVersion"`, so the client did not restrict the TLS version. TLS 1.3 was enforced by the server's `minimum_version` and verified by the per-request `tls_version == tls1.3` check, which passed for every measured request. The results are unaffected. `k6/infer.js` uses the supported `tlsVersion` option.
4. **Payload fixtures** are regenerated deterministically and not committed. Their SHA-256 hashes are recorded in each run manifest.
5. **Statistical method** (post-pilot). The planned unpaired Mann–Whitney U test is replaced as the primary test by a paired run-level analysis (block-paired log ratios, *t*-based CIs, and Holm correction within each metric family). With 5 versus 5 runs, Mann–Whitney's smallest attainable p (≈ 0.008) leaves almost no room under Holm across 9 cells, and it ignores the randomized block structure. Mann–Whitney is kept as a robustness check. See research-design.md §5.
6. **Client timer** (post-pilot). `exec.instance.currentTestRunDuration` (monotonic, sub-µs) replaces `Date.now()` for client encrypt and decrypt timing. This partly supersedes deviation 2.
7. **Health check start interval** (post-pilot). `compose.yaml` sets `start_period: 30s` and `start_interval: 250ms`, so readiness after a per-run restart is detected in about 1 s rather than 5 s. After the first success, the 5 s interval from the Dockerfile applies, as in the pilot.

8. **Open-loop run structure** (post-pilot, during validation; approved by the owner). After the first three open-loop validation attempts, the open-loop run was split into a ramped, drained warm-up scenario and a constant-rate measurement scenario. The VU pool was sized to absorb 100 ms stalls (700 / 190 / 64), and the open-loop sample interval was changed to 23 / 23 / 3. See architecture.md §6.2 and reports/pilot.md §10.3.

9. **Phase 4 design** (approved by the owner on 2026-10-21, before any H5 data).
   - **Response lengths.** Two lengths, 32 and 256 tokens, replace the single 128-token level. The short level is the worst case for H5, and the pair separates fixed cost from per-token cost.
   - **Concurrency.** The concurrency-4 cell is deferred. GPU queueing and batching would make the work unequal between modes.
   - **Interleaved runs.** Modes alternate inside each run, unlike Experiments A and B. One shared GPU dominates latency, and the 1% threshold claim needs drift between runs removed.
   - **Prompt order.** It is counterbalanced, because of Ollama's prompt cache.
   - Full protocol: research-design.md §7.

## Phase 3: Evidence

| # | Deliverable |
|---|---|
| 3.1 | Run `make test` and `make smoke`, and record the output |
| 3.2 ☑ | Experiment A, the full closed-loop matrix: 2 modes × 3 sizes × 3 concurrency levels × 5 repetitions = 90 runs (`make bench`); `results/raw/20261020T233709Z-full`, 90/90 valid |
| 3.2b ☑ | Experiment B, open loop: 2 modes × 3 sizes × 5 repetitions = 30 runs (`make openloop`), analysed separately; `results/raw/20261021T013349Z-openloop`, 30/30 valid |
| 3.3 ☑ | `analysis/aggregate.py` computes p50/p95/p99, throughput, errors, CPU, and memory per cell |
| 3.4 ☑ | `analysis/stats.py`: paired run-level effect sizes, CIs, and Holm correction (implemented and tested in Phase 2; applied to full data in Phase 3) |
| 3.5 ☑ | Figures: implemented as the static dashboard `analysis/dashboard.py` (forest plots, per-run dot plots, server breakdown, sample ECDFs) in place of `analysis/plots.py` |
| 3.5b ☑ | Post-hoc Prometheus and Grafana dashboards backfilled from recorded results (approved 2026-10-20; never live during a benchmark) |
| 3.6 ◐ | `reports/results.md` covers environment, exact commands, results tables, dashboards, limitations, and next steps (draft; awaiting owner review) |

## Phase 4: Optional local inference

| # | Deliverable |
|---|---|
| 4.0 ☑ | Owner approval of the continuation plan (2026-10-21): H5 operational definition, interleaved runs, response lengths 32/256, concurrency 4 deferred, Ollama process and GPU counters recorded, branch `phase-4-inference`. Protocol pre-registered in research-design.md §7 before any H5 data. |
| 4.1 | `/v1/infer` endpoint calling Ollama `qwen3:8b` with `temperature=0`, `seed=42`, `think=false`, `num_predict` ∈ {32, 256} (deviation 9), and a fixed prompt set |
| 4.2 | One virtual user, two response lengths, interleaved modes, five blocks (deviation 9). The concurrency-4 cell is deferred. |
| 4.3 | Separate reporting of inference time (from Ollama's `eval_duration` / `total_duration` and the `infer` server-timing value) and encryption time |
| 4.4 ◐ | Determinism check ☑ (40/40 to the token limit, byte-identical repeats), then the pilot ☑ (`infer-pilot`, 6/6 valid; [reports/infer-pilot.md](../reports/infer-pilot.md)), then the owner's gate (G7 ☑ approved 2026-10-21: full run unchanged, GPU counters kept on, pilot issues 1–2 handled in reporting only), then the full run (`infer`, gated) |
| 4.6 | Research notes (not paper text): [security analysis](notes/security-analysis.md), [frontier and agentic implications](notes/frontier-implications.md), [verified references](notes/references.md) |
| 4.5 | G8 ☑ approved 2026-10-21. After H5: an in-process breakdown of envelope cost (AES-GCM vs Base64 vs JSON), with no change to the API. Protocol in research-design.md §8. |

## Decisions for owner review

| # | Decision | Proposed default | Alternative |
|---|---|---|---|
| D1 | How to obtain k6 | `grafana/k6` Docker image, pinned by digest, so nothing is installed on the host | `brew install k6` on the host. That removes VM CPU contention but adds a host dependency. |
| D2 | Client-side cryptography in aead mode | k6 WebCrypto encrypts each request fresh, with a new nonce every time (realistic end-to-end cost) | Pre-encrypted, single-use payload pool. This isolates server cost but grows memory with request count. |
| D3 | Matrix size | 3 sizes × 3 concurrency levels × 5 repetitions, 60 s windows (about 2 h) | A smaller matrix for faster iteration |
| D4 | Uvicorn workers | 1, which isolates per-process cost | Several workers, which is more realistic for throughput |
| D5 | GitHub visibility | **Private** repository | Public, if the course requires a public link |
