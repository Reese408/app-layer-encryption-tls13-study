<img src="assets/icon.svg" width="64" height="64" alt="">

# Application-Layer Authenticated Encryption over TLS 1.3: A Controlled Performance Study

> **Status: experiments complete; results report drafted ([reports/results.md](reports/results.md)), awaiting owner review.** Experiment A (90-run closed-loop matrix) and Experiment B (30-run open loop) were run on 2026-10-20/21; all 120 runs pass every pre-specified acceptance criterion. Pilot and validation: [reports/pilot.md](reports/pilot.md).

## Abstract

Transport Layer Security (TLS) 1.3 already gives confidentiality, integrity, and server authentication on each network hop. Some systems also encrypt request and response bodies at the application layer, which is often called *payload encryption* or *message-level encryption*. They do this so the data stays protected past points where TLS is terminated, such as load balancers, API gateways, service-mesh sidecars, and logging middleware. This project measures the performance cost of that extra layer. It compares two configurations of the same HTTPS API, one with AES-256-GCM payload encryption and one without, under controlled and reproducible local conditions. It then sets that cost against the security properties the extra layer actually adds. An optional second stage puts a local large language model (Qwen via Ollama) behind both configurations. The goal there is to show how large the encryption overhead is next to a realistic, compute-heavy workload.

## Research questions

- **RQ1.** How much latency (p50/p95/p99) and throughput cost does AES-256-GCM payload encryption add to an API that is already protected by TLS 1.3?
- **RQ2.** How does that cost change with payload size and client concurrency?
- **RQ3.** What CPU and memory overhead comes with the encryption layer?
- **RQ4.** When the API serves local LLM inference, how large is the encryption overhead compared with total request latency?
- **RQ5.** Which threats does the extra layer address that TLS 1.3 alone does not, and which does it not address?

The hypotheses, variables, and statistical method are in [docs/research-design.md](docs/research-design.md).

## Repository layout

```
.
├── README.md                 Project overview (this file)
├── AGENTS.md                 Rules for human and AI contributors
├── docs/
│   ├── research-design.md    Hypotheses, variables, method, statistics
│   ├── architecture.md       System design and data flow
│   ├── threat-model.md       Assets, adversaries, in/out of scope
│   ├── environment.md        Recorded host and tool inventory
│   └── plan.md               Phased implementation plan and open decisions
├── app/                      FastAPI service (Phase 2)
├── k6/                       Load-test scripts (Phase 2)
├── tests/                    Functional and cryptographic tests (Phase 2)
├── scripts/                  Cert/key generation, run orchestration, resource sampling
├── analysis/                 Python analysis and charting (Phase 3)
├── results/
│   ├── raw/                  Timestamped, unmodified benchmark output (committed)
│   └── processed/            Derived tables (regenerable from raw/)
├── reports/                  Findings write-up and figures (Phase 3)
└── assets/
```

## Technology

| Concern | Choice |
|---|---|
| API framework | FastAPI on Uvicorn, Python 3.12 |
| Transport | TLS 1.3 only, with a locally generated private CA |
| Payload cryptography | AES-256-GCM from the [`cryptography`](https://cryptography.io) library (OpenSSL backend) |
| Load generation | [k6](https://k6.io), run from the official `grafana/k6` container |
| Orchestration | Docker Compose |
| Analysis | Python (pandas, SciPy, matplotlib) |
| Optional inference | Ollama 0.35 with `qwen3:8b`, running on the host |

## Quick start

Prerequisites: Docker Desktop with Compose v2+, [uv](https://docs.astral.sh/uv/), OpenSSL, and Python 3.12. k6 runs in a container, so it does not need to be installed.

```bash
make setup        # venv, local CA/certs, AEAD keys, payload fixtures (all gitignored), images
make test         # unit, functional, cryptographic, TLS 1.3, and analysis tests
make up           # start both API configurations (https://localhost:8443 plain, :8444 aead)
make validate     # TLS 1.3/1.2 negotiation evidence + resource-sampler validation
make smoke        # 12 short runs; fails on any error or integrity mismatch
make pilot        # 24-run pilot -> results/raw/<timestamp>-pilot/
make validate-pipeline   # reduced closed- and open-loop runs exercising the revised pipeline
make analyze      # tables for the latest run -> results/processed/<run>/  (RUN=<dir> to pick one)
make stats RUN=results/raw/<dir>       # paired run-level effect sizes, CIs, Holm correction
make check-run RUN=results/raw/<dir>   # acceptance criteria (restart isolation, samples, drops)
make bench        # Experiment A: full 90-run closed-loop matrix; gated (--allow-full)
make openloop     # Experiment B: 30-run open-loop experiment; gated, analysed separately
make dashboard RUNS="results/raw/<dir> ..."  # static offline HTML dashboard
make grafana RUNS="results/raw/<dir> ..."    # Prometheus (backfilled) + Grafana, http://127.0.0.1:3000
make grafana-down
make down
```

Benchmark parameters live in [`experiment.toml`](experiment.toml), and each run records them in its manifest.

## Ethics and safety

- All traffic stays on the local machine. No third-party systems are tested.
- Keys, certificates, and plaintext payloads are never logged or committed.
- All cryptography comes from established libraries. No primitives are implemented here.
- Every reported number can be traced to a raw output file in `results/raw/`.

## License and attribution

Coursework research project by [@Reese408](https://github.com/Reese408).
