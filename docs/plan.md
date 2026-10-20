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
| ☐ | **Owner review of the pilot** ← *current gate before the full matrix* | |

### Deviations from the Phase 1 design (recorded during implementation)

1. **Per-request k6 CSV dropped.** At more than 10⁴ requests per second it would produce about 10⁷ rows per run. The k6 end-of-test summary, whose percentiles are exact over all measured samples, is the raw per-run record.
2. **Client-side crypto timing.** k6 v2.3 has no `performance.now()`. Per-request client encrypt/decrypt timers have 1 ms resolution and are reported as means only. A high-resolution figure comes from `iteration_duration − http_req_duration` and from a loop-timed microbenchmark.
3. **TLS max version option.** k6 v2.3 rejects `maxVersion`. TLS 1.3 is enforced by k6's `minVersion`, by the server's `minimum_version`, and by a per-request `tls_version` check.
4. **Payload fixtures** are regenerated deterministically and not committed. Their SHA-256 hashes are recorded in each run manifest.

## Phase 3: Evidence

| # | Deliverable |
|---|---|
| 3.1 | Run `make test` and `make smoke`, and record the output |
| 3.2 | Full matrix: 2 modes × 3 sizes × 3 concurrency levels × 5 repetitions = 90 runs (about 2 hours) |
| 3.3 | `analysis/aggregate.py` computes p50/p95/p99, throughput, errors, CPU, and memory per cell with bootstrap CIs |
| 3.4 | `analysis/stats.py` runs Mann–Whitney U with Holm correction |
| 3.5 | `analysis/plots.py` draws latency-vs-size, throughput-vs-concurrency, server-timing breakdown, and CPU charts |
| 3.6 | `reports/findings.md` covers environment, exact commands, results tables, figures, limitations, and next steps |

## Phase 4: Optional local inference

| # | Deliverable |
|---|---|
| 4.1 | `/v1/infer` endpoint calling Ollama `qwen3:8b` with `temperature=0`, `seed=42`, `num_predict=128`, `think=false`, and a fixed prompt set |
| 4.2 | A reduced matrix (two modes, concurrency 1 and 4, five repetitions), because inference is slow and serializes on the GPU |
| 4.3 | Separate reporting of inference time (from Ollama's `eval_duration` / `total_duration` and the `infer` server-timing value) and encryption time |

## Decisions for owner review

| # | Decision | Proposed default | Alternative |
|---|---|---|---|
| D1 | How to obtain k6 | `grafana/k6` Docker image, pinned by digest, so nothing is installed on the host | `brew install k6` on the host. That removes VM CPU contention but adds a host dependency. |
| D2 | Client-side cryptography in aead mode | k6 WebCrypto encrypts each request fresh, with a new nonce every time (realistic end-to-end cost) | Pre-encrypted, single-use payload pool. This isolates server cost but grows memory with request count. |
| D3 | Matrix size | 3 sizes × 3 concurrency levels × 5 repetitions, 60 s windows (about 2 h) | A smaller matrix for faster iteration |
| D4 | Uvicorn workers | 1, which isolates per-process cost | Several workers, which is more realistic for throughput |
| D5 | GitHub visibility | **Private** repository | Public, if the course requires a public link |
